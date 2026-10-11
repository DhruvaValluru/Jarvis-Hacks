"""python -m jarvis.app: Alfred's desktop app. A window over the local UI (server.py), a tray icon,
a global Stop hotkey, and one running copy per Windows user.

  python -m jarvis.app               the app
  python -m jarvis.app --fake-core   a scripted engine: no Claude, no Windows, separate test settings
  python -m jarvis.app --no-window   use the default browser instead of Alfred's own window
  python -m jarvis.app --port 8770   a fixed port (default: a random free one)

The window needs pywebview and the tray icon pystray (with Pillow). Without pywebview, or without
the WebView2 runtime it needs, Alfred opens in the browser; without pystray there is no tray icon,
and closing the window quits, so Alfred never runs where you can't see it.
"""

import argparse
import http.client
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .. import config
from .server import App

HOTKEY = "Ctrl+Alt+Backspace"
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_BACK, WM_HOTKEY, WM_QUIT = 0x08, 0x0312, 0x0012
SM_SHUTTINGDOWN = 0x2000


class InstanceLock:
    """One Alfred per user: an OS file lock in data_dir()/run, plus alfred.json with the running
    copy's port and instance token so a second launch can ask it to show its window."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.info_path = self.folder / "alfred.json"
        self._fd = None

    def acquire(self) -> bool:
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / "alfred.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def publish(self, port: int, token: str) -> None:
        tmp = self.info_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": port, "token": token}, f)
        os.replace(tmp, self.info_path)

    def read(self) -> dict | None:
        try:
            info = json.loads(self.info_path.read_text(encoding="utf-8"))
            return info if isinstance(info, dict) else None
        except (OSError, ValueError):
            return None

    def show_running(self, timeout: float = 5) -> bool:
        """Ask the copy that holds the lock to show its window. False if it doesn't answer."""
        deadline = time.monotonic() + timeout
        while True:
            info = self.read()
            if info:
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", int(info["port"]), timeout=5)
                    conn.request("POST", "/api/instance/show", body=b"{}",
                                 headers={"X-Alfred-Instance": str(info["token"]), "Content-Type": "application/json"})
                    ok = conn.getresponse().status == 200
                    conn.close()
                    if ok:
                        return True
                except (OSError, ValueError, KeyError, http.client.HTTPException):
                    pass
            if time.monotonic() > deadline:
                return False
            time.sleep(0.25)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            self.info_path.unlink(missing_ok=True)
        except OSError:
            pass
        if sys.platform == "win32":
            import msvcrt

            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(self._fd)  # also drops a POSIX flock
        self._fd = None


class StopHotkey:
    """Ctrl+Alt+Backspace stops Alfred from any app: RegisterHotKey on its own thread (Windows only)."""

    keys = HOTKEY

    def __init__(self, on_press):
        self.on_press = on_press
        self.ok = False
        self.error = ""
        self._thread_id = 0  # only while its message loop runs: Windows reuses thread ids
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start(self, timeout: float = 2) -> "StopHotkey":
        threading.Thread(target=self._run, daemon=True, name="alfred-hotkey").start()
        if not self._ready.wait(timeout):
            self.error = "The Stop hotkey didn't start. Use the Stop button or the tray icon."
        return self

    def _run(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_BACK):
                self.error = f"{HOTKEY} is taken by another app, so the Stop hotkey is off. Use the Stop button."
                return
            with self._lock:  # RegisterHotKey gave this thread a message queue
                self._thread_id = kernel32.GetCurrentThreadId()
            self.ok = True
        except Exception as e:  # noqa: BLE001 - the app works without it; say so in the window
            self.error = f"The Stop hotkey couldn't be set up ({type(e).__name__}). Use the Stop button."
            return
        finally:
            self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception:  # noqa: BLE001 - keep listening
                        pass
        finally:
            with self._lock:
                self._thread_id = 0
            user32.UnregisterHotKey(None, 1)
            self.ok = False

    def stop(self) -> None:
        with self._lock:
            if self._thread_id:
                import ctypes

                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


class Shell:
    """The window, tray icon and hotkey around one App. run() returns once Alfred quits."""

    def __init__(self, app: App, service, *, window: bool = True):
        self.app, self.service = app, service
        self.use_window = window
        self.window = None
        self.window_failed = not window  # True once Alfred uses the browser instead
        self.tray = None
        self.hotkey = None
        self.quitting = threading.Event()
        self._minimized = None  # None: pywebview doesn't say, so show() always restores
        self._told_tray = False
        self._unsubscribe = None
        app.on_show, app.on_quit = self.show, self.quit

    def run(self) -> int:
        if sys.platform == "win32":
            self.hotkey = self.app.hotkey = StopHotkey(lambda: self.service.stop("hotkey")).start()
        self.tray = self._start_tray()
        self._unsubscribe = self.service.subscribe(self._on_event)
        try:
            if not (self.use_window and self._run_window()):
                self._run_browser()
        finally:
            self._cleanup()
        return 0

    def show(self) -> None:
        if self.quitting.is_set():
            return
        if not self.window_failed:
            window = self.window
            if window is None:
                return  # Alfred's window is still opening, and shows itself (or the browser opens instead)
            try:
                window.show()
                if self._minimized is not False:
                    window.restore()  # only when needed: it also un-maximizes
                return
            except Exception:  # noqa: BLE001 - fall back to the browser
                pass
        url = self.app.login_url()
        print(f"Opening Alfred: {url}")
        _open_browser(url)

    def quit(self) -> None:
        self.quitting.set()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001 - already gone
                pass

    def notify(self, text: str) -> None:
        try:
            print(text)
        except (OSError, UnicodeError):  # a console that can't show some character
            pass
        if self.tray is not None:
            try:
                self.tray.notify(text, "Alfred")
            except Exception:  # noqa: BLE001 - notifications are a nicety
                pass

    # --- window ---------------------------------------------------------------
    def _run_window(self) -> bool:
        try:
            import webview
        except ImportError:
            print("pywebview isn't installed, so Alfred opens in your browser. "
                  "For its own window: pip install pywebview")
            self.window_failed = True
            return False
        try:
            if _missing_webview2():
                self.window_failed = True
                self.notify("Alfred's own window needs the Microsoft Edge WebView2 Runtime, which this PC doesn't "
                            "have, so Alfred opens in your browser. Install the runtime from Microsoft to get the window.")
                return False
            self.window = webview.create_window("Alfred", self.app.login_url(), width=1040, height=760,
                                                min_size=(440, 560), background_color="#11141a")
            events = self.window.events
            events.closing += self._on_closing
            try:
                events.minimized += self._on_minimized
                events.restored += self._on_restored
                events.maximized += self._on_restored
                self._minimized = False
            except AttributeError:
                pass
            icon = window_icon()
            webview.start(**({"icon": icon} if icon else {}))
        except Exception as e:  # noqa: BLE001 - no display, pythonnet missing, ...
            print(f"Alfred's window couldn't open ({type(e).__name__}: {e}). Using your browser instead.")
            self.window = None
            self.window_failed = True
            return self.quitting.is_set()
        return True

    def _on_minimized(self) -> None:
        self._minimized = True

    def _on_restored(self) -> None:
        self._minimized = False

    def _on_closing(self):
        # No tray icon: closing the window quits, so Alfred never runs unseen. Windows signing out or
        # shutting down: never hold that up (pywebview doesn't say why the window is closing).
        if self.quitting.is_set() or not getattr(self.tray, "visible", False) or _session_ending():
            self.quitting.set()
            return True
        # pywebview waits for this handler on the window's thread, so hide the window from another one.
        threading.Timer(0.05, self._hide).start()
        self.app.end_voice("closed")  # the page lives on in the tray, but the microphone mustn't
        if not self._told_tray:
            self._told_tray = True
            self.notify("Alfred is still running in the tray. Use its icon to open Alfred, stop it or quit.")
        return False

    def _hide(self) -> None:
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001
            pass

    def _run_browser(self) -> None:
        url = self.app.login_url()
        print(f"Alfred is running at {self.app.origin}")
        print(f"Opening it in your browser. If it doesn't open, use this link (it works once): {url}")
        _open_browser(url)
        print("To quit: Quit in the tray icon or in Alfred's Settings, or Ctrl+C here.")
        try:
            while not self.quitting.wait(0.5):
                pass
        except KeyboardInterrupt:
            self.quitting.set()

    # --- tray -------------------------------------------------------------------
    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        try:
            icon = pystray.Icon("Alfred", tray_image(Image, ImageDraw), "Alfred", menu=pystray.Menu(
                pystray.MenuItem("Open Alfred", lambda: self.show(), default=True),
                pystray.MenuItem("Stop everything", lambda: self.service.stop("tray")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: self.quit()),
            ))
            threading.Thread(target=icon.run, daemon=True, name="alfred-tray").start()
        except Exception as e:  # noqa: BLE001 - no tray on this desktop
            print(f"No tray icon ({type(e).__name__}); closing the window quits Alfred.")
            return None
        return icon

    def _on_event(self, event: dict) -> None:
        """On the thread that published the event: keep it quick."""
        kind = event.get("type")
        if kind == "state" and self.tray is not None:
            try:
                self.tray.title = f"Alfred: {STATE_LABELS.get(event.get('state'), event.get('state'))}"
            except Exception:  # noqa: BLE001
                pass
        elif kind == "approval" and event.get("source") != "telegram":  # Telegram asks in Telegram
            threading.Thread(target=self._needs_ok, args=(str(event.get("reason") or ""),), daemon=True).start()

    def _needs_ok(self, reason: str) -> None:
        """Someone at this PC has to answer: bring the window forward even if it's in the tray."""
        if self.window is not None:
            self.show()
        self.notify(f"Alfred needs your OK: {reason[:200]}")

    def _cleanup(self) -> None:
        self.quitting.set()
        print("Alfred is closing...")
        if self.hotkey is not None:
            self.hotkey.stop()
        if self._unsubscribe is not None:
            self._unsubscribe()
        try:
            self.service.shutdown()
        finally:
            if self.tray is not None:
                try:
                    self.tray.stop()
                except Exception:  # noqa: BLE001
                    pass
            self.app.close()


STATE_LABELS = {"off": "off", "needs_consent": "needs your OK", "starting": "starting", "ready": "ready",
                "busy": "working", "stopping": "stopping", "crashed": "crashed"}


LOGO = Path(__file__).resolve().parent / "ui" / "logo.png"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def window_icon() -> str | None:
    """Alfred's logo as an .ico for the window and its taskbar button. Without one, pywebview shows the icon
    Windows has for Alfred.exe, and Windows can keep showing an old one from its icon cache after an update.
    Installed: the alfred.ico next to Alfred.exe (packaging/alfred.spec). From source: made from ui/logo.png."""
    if config.frozen():
        path = config.app_dir() / "alfred.ico"
        return str(path) if path.is_file() else None
    path = config.data_dir() / "alfred.ico"
    try:
        if not path.is_file() or path.stat().st_mtime < LOGO.stat().st_mtime:
            from PIL import Image

            path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(LOGO) as logo:
                logo.convert("RGBA").save(path, format="ICO", sizes=[(n, n) for n in ICON_SIZES])
        return str(path)
    except (OSError, ValueError, ImportError):
        return None


def tray_image(Image, ImageDraw, logo=LOGO):
    """Alfred's tray icon: the logo (ui/logo.png), or a glossy sky-blue disc if that can't be read."""
    try:
        with Image.open(logo) as img:
            return img.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    sky = Image.linear_gradient("L").resize((64, 64))
    disc = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(disc).ellipse((2, 2, 61, 61), fill=255)
    img.paste(Image.composite(Image.new("RGBA", (64, 64), (16, 108, 210, 255)),
                              Image.new("RGBA", (64, 64), (156, 222, 255, 255)), sky), (0, 0), disc)
    gloss = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse((11, 5, 53, 30), fill=(255, 255, 255, 90))
    img.alpha_composite(gloss)
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 61, 61), outline=(10, 72, 150, 255), width=2)
    white = (255, 255, 255, 255)
    draw.line((19, 49, 32, 14, 45, 49), fill=white, width=6, joint="curve")
    draw.line((25, 37, 39, 37), fill=white, width=5)
    return img


def _missing_webview2(platform: str = sys.platform) -> bool:
    """Without the WebView2 runtime pywebview quietly uses Internet Explorer (MSHTML), which can't run
    Alfred's page; it doesn't raise. Look in the registry first: importing pywebview's backend on a PC
    without WebView2 writes Internet Explorer settings. Then ask which engine it picked."""
    if platform != "win32":
        return False
    from .. import selftest

    try:
        if not selftest.webview2_version():
            return True
    except Exception:  # noqa: BLE001 - can't tell from the registry; pywebview's own answer decides
        pass
    from webview.platforms import winforms

    return getattr(winforms, "renderer", "") == "mshtml"


def _session_ending() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))
    except Exception:  # noqa: BLE001
        return False


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the link is printed too
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.app", description="Alfred's desktop app.")
    parser.add_argument("--fake-core", action="store_true", help="scripted engine for UI work (no Claude, no Windows)")
    parser.add_argument("--no-window", action="store_true", help="open in the default browser instead of a window")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a random free one)")
    args = parser.parse_args(argv)

    lock = InstanceLock(config.data_dir() / "run")
    if not lock.acquire():
        if lock.show_running():
            print("Alfred is already running; showing its window.")
            return 0
        print("Alfred is already running but didn't answer. "
              "Quit it from its tray icon (or Task Manager), then try again.")
        return 1
    try:
        from ..settings import MemoryBackend, Store

        if args.fake_core:
            from ..service.fake import FakeService

            store = Store(config.data_dir() / "fake-settings.env", MemoryBackend())  # never your real keys
            service = FakeService(store, consent=None)
        else:
            from ..service import AlfredService

            store = Store()
            service = AlfredService(store)
        app = App(service, store=store)
        app.autostart_telegram = not args.fake_core
        try:
            app.start(args.port)
        except OSError as e:
            print(f"Alfred couldn't listen on 127.0.0.1:{args.port} ({e}). Try another --port, or leave it out.")
            return 1
        lock.publish(app.port, app.instance_token)
        shell = Shell(app, service, window=not args.no_window)
        app.start_service()
        return shell.run()
    finally:
        lock.release()

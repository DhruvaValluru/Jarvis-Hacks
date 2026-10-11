"""Microphones and the local speech model, for the setup.

The model (JARVIS_WHISPER_MODEL, default base.en) downloads with huggingface_hub.snapshot_download
into config.models_dir(), where voice.Listener loads it from. The download runs in its own process,
shown in the setup with its progress, so Cancel really stops it (and Alfred quitting does too). A
cancelled download continues where it stopped next time.

  python -m jarvis.onboarding.speech NAME FOLDER   the download itself (JSON lines on stdout)
"""

import fnmatch
import json
import os
import sys
import threading
import time

from .. import config, voice

# The files faster-whisper fetches (faster_whisper.utils.download_model).
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
APPROX_BYTES = {"tiny.en": 75_000_000, "base.en": 145_000_000, "small.en": 484_000_000}


def microphones() -> dict:
    """{"available", "devices": [{"name", "default"}], "error"} without failing when there is no sound system."""
    try:
        devices = [{"name": d["name"], "default": d["default"]} for d in voice.input_devices()]
    except ImportError:
        return {"available": False, "devices": [], "error": "The voice extras aren't installed, so Alfred can't list "
                                                            "microphones. Run setup.bat to add them."}
    except Exception as e:  # noqa: BLE001 - PortAudio errors and the like
        return {"available": False, "devices": [], "error": f"Couldn't list microphones ({type(e).__name__})."}
    if not devices:
        return {"available": False, "devices": [], "error": "No microphone found. Plug one in, or skip this: typing always works."}
    return {"available": True, "devices": devices, "error": ""}


def present(name: str) -> bool:
    return voice.model_root(name) is not None


def download_command(name: str, folder: str) -> list[str]:
    """Like service.engine_command(): from source this Python, installed the bundled alfred-cli.exe."""
    if config.frozen():
        return [str(config.app_dir() / "alfred-cli.exe"), "--fetch-speech-model", name, folder]
    return [sys.executable, "-u", "-m", "jarvis.onboarding.speech", name, folder]


def folder_bytes(path) -> int:
    total = 0
    try:
        for file in path.rglob("*"):
            try:
                if file.is_file() and not file.is_symlink():
                    total += file.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


class ModelDownload:
    """One download at a time. progress() is what the setup polls: state is idle, downloading, done,
    error or cancelled."""

    def __init__(self, command=download_command):
        self.command = command
        self.state = "idle"
        self.model = ""
        self.error = ""
        self.total = 0
        self._child = None
        self._base = 0  # bytes already in the folder when this download started
        self._lock = threading.Lock()

    def start(self, name: str) -> dict:
        from ..service.process import Child

        with self._lock:
            if self.state == "downloading":
                raise RuntimeError("The speech model is already downloading.")
            self.model, self.error, self.total = name, "", APPROX_BYTES.get(name, 0)
            self._base = folder_bytes(voice.model_folder(name))
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                   "HF_HUB_DISABLE_PROGRESS_BARS": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                   # Plain HTTP downloads into the model folder: progress shows as the files grow there, and
                   # no Xet transfer threads keep the process alive after it finished.
                   "HF_HUB_DISABLE_XET": "1"}
            try:
                child = Child(self.command(name, str(config.models_dir())), cwd=str(config.app_dir()), env=env)
            except OSError as e:
                self.state, self.error = "error", f"Couldn't start the download ({type(e).__name__})."
                return self.progress()
            self._child, self.state = child, "downloading"
        threading.Thread(target=self._read, args=(child,), daemon=True, name="alfred-model-download").start()
        threading.Thread(target=self._drain, args=(child,), daemon=True, name="alfred-model-download-err").start()
        return self.progress()

    def _drain(self, child) -> None:
        """Read the download's warnings so it never blocks on a full pipe; they aren't shown."""
        try:
            for _line in child.stderr:
                pass
        except (OSError, ValueError):
            pass

    def cancel(self) -> dict:
        with self._lock:
            child = self._child
            if child is None or self.state != "downloading":
                return self.progress()
            self.state = "cancelled"
        child.kill_tree()
        return self.progress()

    def wait(self, timeout: float = 30) -> bool:
        """For tests: True once the download process has ended."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._child is None:
                return True
            time.sleep(0.02)
        return False

    def progress(self) -> dict:
        name = self.model
        done = folder_bytes(voice.model_folder(name)) if name else 0
        total = self.total
        if self.state == "done":
            percent = 100
        elif total:
            percent = min(99, int(100 * max(0, done - self._base) / max(1, total - self._base)))
        else:
            percent = None
        return {"state": self.state, "model": name, "bytes": done, "total": total, "percent": percent,
                "error": self.error}

    def _read(self, child) -> None:
        result = {}
        try:
            for line in child.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "total" and isinstance(message.get("bytes"), int) and message["bytes"] > 0:
                    self.total = message["bytes"]
                elif message.get("type") in ("done", "error"):
                    result = message
                    break  # the answer is in: don't wait for the process to close its output as well
        except (OSError, ValueError):
            pass
        code = child.wait(10)
        if code is None and result.get("type") == "done":
            code = 0  # finished but still shutting down (a library's threads): the model is there, so it's done
        child.close()  # also ends a process that's still around
        with self._lock:
            if self._child is not child:
                return  # a cancelled download, and Download was pressed again meanwhile: not ours to finish
            self._child = None
            if self.state != "downloading":
                return  # cancelled
            if code == 0 and result.get("type") == "done":
                self.state = "done"
            else:
                self.state = "error"
                self.error = str(result.get("error") or f"The download stopped (exit code {code}).")[:300]


# --- the download process ---------------------------------------------------------------
def fetch(name: str, folder: str, say=None) -> int:
    """Download `name` into `folder` (a huggingface_hub cache folder). Prints JSON lines:
    {"type": "total", "bytes"} when known, then {"type": "done"} or {"type": "error", "error"}."""
    say = say or (lambda **message: print(json.dumps(message), flush=True))
    try:
        import huggingface_hub
    except ImportError:
        say(type="error", error="The voice extras aren't installed (huggingface_hub). Run setup.bat to add them.")
        return 1
    repo = voice.model_repo(name)
    try:
        info = huggingface_hub.HfApi().model_info(repo, files_metadata=True)
        total = sum(f.size or 0 for f in info.siblings or [] if any(fnmatch.fnmatch(f.rfilename, p) for p in FILES))
        if total:
            say(type="total", bytes=total)
    except Exception:  # noqa: BLE001 - progress then uses an estimate
        pass
    try:
        huggingface_hub.snapshot_download(repo, cache_dir=folder, allow_patterns=FILES)
    except Exception as e:  # noqa: BLE001
        say(type="error", error=_failure(e))
        return 1
    say(type="done")
    return 0


def _failure(error: Exception) -> str:
    kind = type(error).__name__
    if isinstance(error, (OSError, TimeoutError)) or "Connect" in kind or "Timeout" in kind or "Offline" in kind:
        return "Couldn't reach Hugging Face to download the speech model: check your internet connection, then try again."
    return f"The speech model didn't download ({kind}). Try again later."


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m jarvis.onboarding.speech MODEL FOLDER", file=sys.stderr)
        return 2
    return fetch(*args)


def run_and_exit(argv=None) -> None:
    """The download process's entry point: exit as soon as the answer is printed. A download library can
    leave threads running that would otherwise keep the process (and the setup's progress) waiting."""
    code = main(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    os._exit(code)


if __name__ == "__main__":
    run_and_exit()

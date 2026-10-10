"""Claude computer use: screenshots plus the real mouse and keyboard.

This is the vision fallback for apps UI Automation can't read (games, canvases,
custom-drawn UIs). Claude calls the members of the `computer_toolset_20260801`
toolset (screenshot, left_click, type, key, scroll, zoom, ...); run() executes
one call. Coordinates arrive in the pixel space of the screenshots we send
(about 1280 px wide), and Geometry maps them to the real screen.
"""

import base64
import io
import time
from dataclasses import dataclass
from typing import Any, Callable

from .registry import Image, ToolError, audit
from .uia import RISKY_WORDS

TOOLSET_NAME = "computer"
TOOLSET = {"type": "computer_toolset_20260801"}
HALTED = "Not executed: an earlier computer action in this turn failed."
LONG_EDGE = 1280  # screenshot size sent to Claude; well inside the API's image limits
SETTLE = 0.15  # seconds to let the UI react after an input action
MAX_WAIT = 60  # cap for wait / hold_key; the API allows 300 but this is a voice assistant


class Aborted(Exception):
    """The user took the mouse back (moved it into the top-left corner)."""


@dataclass(frozen=True)
class Geometry:
    """Maps between screenshot pixels (what Claude sees) and screen pixels."""

    width: int
    height: int
    long_edge: int = LONG_EDGE

    @property
    def shot_size(self) -> tuple[int, int]:
        scale = min(1.0, self.long_edge / max(self.width, self.height))
        return max(1, round(self.width * scale)), max(1, round(self.height * scale))

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        sw, sh = self.shot_size
        sx = round(x * self.width / sw)
        sy = round(y * self.height / sh)
        return min(max(sx, 0), self.width - 1), min(max(sy, 0), self.height - 1)

    def to_shot(self, x: float, y: float) -> tuple[int, int]:
        sw, sh = self.shot_size
        return round(x * sw / self.width), round(y * sh / self.height)


# xdotool-style key names (what Claude uses) -> Windows virtual-key codes.
VK_NAMES = {
    "return": 0x0D, "enter": 0x0D, "kp_enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "space": 0x20, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "prior": 0x21, "pageup": 0x21,
    "page_down": 0x22, "next": 0x22, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "control_l": 0xA2, "control_r": 0xA3,
    "shift": 0x10, "shift_l": 0xA0, "shift_r": 0xA1,
    "alt": 0x12, "alt_l": 0xA4, "alt_r": 0xA5, "option": 0x12,
    "super": 0x5B, "super_l": 0x5B, "super_r": 0x5C, "win": 0x5B, "windows": 0x5B, "meta": 0x5B, "cmd": 0x5B,
    "menu": 0x5D, "print": 0x2C, "caps_lock": 0x14, "num_lock": 0x90, "scroll_lock": 0x91, "pause": 0x13,
    "xf86audioplay": 0xB3, "xf86audionext": 0xB0, "xf86audioprev": 0xB1, "xf86audiostop": 0xB2,
    "xf86audiomute": 0xAD, "xf86audiolowervolume": 0xAE, "xf86audioraisevolume": 0xAF,
    "minus": 0xBD, "plus": 0xBB, "equal": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "semicolon": 0xBA, "apostrophe": 0xDE, "grave": 0xC0, "bracketleft": 0xDB, "bracketright": 0xDD,
    "backslash": 0xDC,
}
VK_NAMES.update({f"f{n}": 0x6F + n for n in range(1, 25)})
SHIFT = 0x10


def parse_combo(combo: str, vk_for_char: Callable[[str], tuple[int, bool] | None] | None = None) -> list[int]:
    """'ctrl+shift+t' -> [VK_CONTROL, VK_SHIFT, ord('T')]. Raises ToolError on unknown keys."""
    text = combo.strip()
    if len(text) == 1:
        parts = [text]
    elif text.endswith("++"):  # "ctrl++"
        parts = text[:-2].split("+") + ["+"]
    else:
        parts = text.split("+")
    keys: list[int] = []
    for name in (p.strip() for p in parts if p.strip()):
        low = name.lower()
        if low in VK_NAMES:
            keys.append(VK_NAMES[low])
        elif len(name) == 1 and (name.isascii() and name.isalnum()):
            keys.append(ord(name.upper()))
        elif len(name) == 1 and vk_for_char and (found := vk_for_char(name)):
            vk, shift = found
            if shift and SHIFT not in keys:
                keys.append(SHIFT)
            keys.append(vk)
        else:
            raise ToolError(f"Unknown key '{name}' in '{combo}'.")
    if not keys:
        raise ToolError("No key given.")
    return keys


def _encode_png(img) -> Image:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Image(base64.b64encode(buf.getvalue()).decode("ascii"))


def _point(value: Any, label: str = "coordinate") -> tuple[float, float]:
    try:
        x, y = value
        return float(x), float(y)
    except (TypeError, ValueError):
        raise ToolError(f"{label} must be [x, y], got {value!r}.") from None


class Computer:
    def __init__(self, backend=None):
        self._backend = backend
        self._last_seen: tuple[int, int] | None = None

    @property
    def backend(self):
        if self._backend is None:
            from .wininput import WinBackend

            self._backend = WinBackend()
        return self._backend

    def geometry(self) -> Geometry:
        return Geometry(*self.backend.size())

    def new_session(self) -> None:
        """Called at the start of each request, so the corner check only reacts to new mouse moves."""
        self._last_seen = None

    # --- safety -----------------------------------------------------------
    def _check_desktop(self) -> None:
        name = self.backend.input_desktop()
        if name.lower() != "default":
            raise ToolError(
                "The screen is locked or a Windows security prompt is showing. I can't see or act there; "
                "the user has to handle it."
            )

    def _check_abort(self) -> None:
        x, y = self.backend.cursor()
        in_corner = x <= 2 and y <= 2
        if in_corner and self._last_seen is not None and self._last_seen != (x, y):
            raise Aborted()

    def _check_click(self, at: tuple[int, int] | None, confirm) -> str:
        """Ask before clicking a Send/Delete/Buy-style control, like ui_click does."""
        x, y = at if at else self.backend.cursor()
        try:
            target = self.backend.element_at(x, y)
        except Exception:  # noqa: BLE001 - no UIA info for this pixel
            target = ""
        if target and RISKY_WORDS.search(target) and not (confirm and confirm(f"click {target} on screen")):
            raise ToolError("The user declined this action. Do not retry it; ask what they want instead.")
        return target

    # --- actions ------------------------------------------------------------
    def run(self, name: str, args: dict, confirm: Callable[[str], bool] | None = None) -> tuple[bool, Any]:
        """Run one toolset member. Returns (ok, result). Raises only Aborted."""
        args = {k: v for k, v in (args or {}).items() if v is not None}
        detail: dict = {}
        try:
            self._check_abort()
            self._check_desktop()
            result = self._dispatch(name, args, confirm, detail)
            ok = True
        except Aborted:
            audit(f"computer.{name}", args, False, "aborted: mouse moved to the corner")
            raise
        except ToolError as e:
            ok, result = False, str(e)
        except Exception as e:  # noqa: BLE001 - report any failure back to the model
            ok, result = False, f"{type(e).__name__}: {e}"
        try:
            self._last_seen = self.backend.cursor()
        except Exception:  # noqa: BLE001
            self._last_seen = None
        audit(f"computer.{name}", {**args, **detail}, ok, result)
        return ok, result

    def _dispatch(self, name: str, args: dict, confirm, detail: dict):
        b = self.backend
        geo = self.geometry()

        def target(key: str = "coordinate") -> tuple[int, int] | None:
            if key not in args:
                return None
            pos = geo.to_screen(*_point(args[key], key))
            detail[f"screen_{key}"] = list(pos)
            return pos

        if name == "screenshot":
            img = b.grab()
            if img.size != geo.shot_size:
                img = img.resize(geo.shot_size)
            return _encode_png(img)

        if name == "zoom":
            region = args.get("region")
            if not region or len(region) != 4:
                raise ToolError("zoom needs region [x0, y0, x1, y1].")
            x0, y0 = geo.to_screen(region[0], region[1])
            x1, y1 = geo.to_screen(region[2], region[3])
            if x1 <= x0 or y1 <= y0:
                raise ToolError("zoom region must have x1 > x0 and y1 > y0.")
            img = b.grab().crop((x0, y0, x1 + 1, y1 + 1))
            img.thumbnail((LONG_EDGE, LONG_EDGE))  # full detail, only shrunk if it's bigger than a screenshot
            return _encode_png(img)

        if name == "cursor_position":
            x, y = geo.to_shot(*b.cursor())
            return f"x={x}, y={y}"

        if name == "wait":
            time.sleep(min(float(args.get("duration", 1)), MAX_WAIT))
            return "OK"

        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            pos = target()
            detail["element"] = self._check_click(pos, confirm)
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            count = {"double_click": 2, "triple_click": 3}.get(name, 1)
            mods = parse_combo(args["text"], b.vk_for_char) if args.get("text") else []
            if pos:
                b.move(*pos)
                time.sleep(0.03)
            with _held(b, mods):
                for i in range(count):
                    if i:
                        time.sleep(0.05)
                    b.button(button, True)
                    b.button(button, False)
            time.sleep(SETTLE)
            return "OK"

        if name == "left_click_drag":
            start, end = target("start_coordinate"), target()
            if start is None or end is None:
                raise ToolError("left_click_drag needs start_coordinate and coordinate.")
            detail["element"] = self._check_click(start, confirm)
            mods = parse_combo(args["text"], b.vk_for_char) if args.get("text") else []
            with _held(b, mods):
                b.move(*start)
                time.sleep(0.05)
                b.button("left", True)
                steps = 10
                for i in range(1, steps + 1):  # move in steps so apps see a drag
                    b.move(start[0] + (end[0] - start[0]) * i // steps, start[1] + (end[1] - start[1]) * i // steps)
                    time.sleep(0.02)
                b.button("left", False)
            time.sleep(SETTLE)
            return "OK"

        if name == "mouse_move":
            pos = target()
            if pos is None:
                raise ToolError("mouse_move needs coordinate.")
            b.move(*pos)
            return "OK"

        if name in ("left_mouse_down", "left_mouse_up"):
            if name == "left_mouse_down":
                detail["element"] = self._check_click(None, confirm)
            b.button("left", name == "left_mouse_down")
            time.sleep(SETTLE)
            return "OK"

        if name == "scroll":
            direction = args.get("scroll_direction", "down")
            if direction not in ("up", "down", "left", "right"):
                raise ToolError(f"Unknown scroll_direction {direction!r}.")
            amount = max(1, min(int(args.get("scroll_amount", 3)), 50))
            pos = target()
            if pos:
                b.move(*pos)
            mods = parse_combo(args["text"], b.vk_for_char) if args.get("text") else []
            sign = 1 if direction in ("up", "right") else -1
            with _held(b, mods):
                b.wheel(sign * amount, horizontal=direction in ("left", "right"))
            time.sleep(SETTLE)
            return "OK"

        if name == "type":
            text = args.get("text", "")
            if b.focused_is_password():
                raise ToolError(
                    "The focused field is a password field. Jarvis doesn't type passwords; "
                    "ask the user to enter it themselves."
                )
            b.type_text(text)
            time.sleep(SETTLE)
            return "OK"

        if name in ("key", "hold_key"):
            keys = parse_combo(args.get("text", ""), b.vk_for_char)
            repeat = max(1, min(int(args.get("repeat", 1)), 100)) if name == "key" else 1
            for _ in range(repeat):
                with _held(b, keys):
                    if name == "hold_key":
                        time.sleep(min(float(args.get("duration", 1)), MAX_WAIT))
                time.sleep(0.02)
            time.sleep(SETTLE)
            return "OK"

        raise ToolError(f"Unknown computer action: {name}")


class _held:
    """Hold keys down for the duration of a with-block (released in reverse order)."""

    def __init__(self, backend, keys: list[int]):
        self.backend, self.keys = backend, keys

    def __enter__(self):
        for k in self.keys:
            self.backend.key(k, True)

    def __exit__(self, *exc):
        for k in reversed(self.keys):
            self.backend.key(k, False)
        return False


_computer = Computer()


def set_backend(backend) -> None:
    """Swap the backend (tests use a fake one)."""
    global _computer
    _computer = Computer(backend)


def new_session() -> None:
    _computer.new_session()


def run(name: str, args: dict, confirm: Callable[[str], bool] | None = None) -> tuple[bool, Any]:
    return _computer.run(name, args, confirm)


def halted_result(tool_use_id: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "toolset_name": TOOLSET_NAME,
            "is_error": True, "content": HALTED}

"""Windows backend for computer use: screen capture plus real mouse and keyboard input.

Everything here works in physical pixels of the primary monitor, which is why
enable_dpi_awareness() must run first (jarvis/__main__.py does it at startup).
"""

import ctypes
import time
from ctypes import wintypes

ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
BUTTON_FLAGS = {  # button -> (down, up)
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
# Keys that need KEYEVENTF_EXTENDEDKEY, or apps read them as numpad keys.
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}
VK_RETURN, VK_TAB = 0x0D, 0x09


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def enable_dpi_awareness() -> None:
    """Use physical pixels everywhere, so screenshots and clicks line up on scaled displays."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def _user32():
    u = ctypes.windll.user32
    u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    u.SendInput.restype = wintypes.UINT
    u.OpenInputDesktop.restype = ctypes.c_void_p
    u.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    u.GetUserObjectInformationW.argtypes = (
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    u.CloseDesktop.argtypes = (ctypes.c_void_p,)
    u.VkKeyScanW.restype = ctypes.c_short
    return u


class WinBackend:
    """Talks to Windows. computer.py holds the logic and a fake backend stands in for tests."""

    def __init__(self):
        enable_dpi_awareness()
        self.user32 = _user32()

    # --- screen ---------------------------------------------------------
    def size(self) -> tuple[int, int]:
        return self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)  # primary monitor

    def grab(self):
        from PIL import ImageGrab

        w, h = self.size()
        return ImageGrab.grab(bbox=(0, 0, w, h))

    def input_desktop(self) -> str:
        """Name of the desktop receiving input: 'Default' normally, 'Winlogon' when locked or on UAC."""
        handle = self.user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            self.user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed))  # UOI_NAME
            return buf.value
        finally:
            self.user32.CloseDesktop(handle)

    # --- mouse ----------------------------------------------------------
    def cursor(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move(self, x: int, y: int) -> None:
        self.user32.SetCursorPos(int(x), int(y))

    def _mouse(self, flags: int, data: int = 0) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        self._send([inp])

    def button(self, button: str, down: bool) -> None:
        self._mouse(BUTTON_FLAGS[button][0 if down else 1])

    def wheel(self, notches: int, horizontal: bool = False) -> None:
        self._mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, notches * 120)

    # --- keyboard -------------------------------------------------------
    def _key_input(self, vk: int, down: bool) -> INPUT:
        flags = 0 if down else KEYEVENTF_KEYUP
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(vk, self.user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
        return inp

    def key(self, vk: int, down: bool) -> None:
        self._send([self._key_input(vk, down)])

    def vk_for_char(self, char: str) -> tuple[int, bool] | None:
        """(virtual key, needs shift) for one printable character on the current layout."""
        res = self.user32.VkKeyScanW(ord(char))
        if res == -1:
            return None
        return res & 0xFF, bool(res & 0x100)

    def type_text(self, text: str) -> None:
        """Type any text as Unicode key events, so it doesn't depend on the keyboard layout."""
        lines = text.replace("\r\n", "\n").split("\n")
        for i, line in enumerate(lines):
            if i:
                self._send([self._key_input(VK_RETURN, True), self._key_input(VK_RETURN, False)])
            for j, part in enumerate(line.split("\t")):
                if j:
                    self._send([self._key_input(VK_TAB, True), self._key_input(VK_TAB, False)])
                units = part.encode("utf-16-le")
                codes = [int.from_bytes(units[k:k + 2], "little") for k in range(0, len(units), 2)]
                for start in range(0, len(codes), 20):  # small chunks: some apps drop fast input
                    batch = []
                    for code in codes[start:start + 20]:
                        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                            inp = INPUT(type=INPUT_KEYBOARD)
                            inp.u.ki = KEYBDINPUT(0, code, flags, 0, 0)
                            batch.append(inp)
                    self._send(batch)
                    time.sleep(0.01)

    def _send(self, inputs: list) -> None:
        arr = (INPUT * len(inputs))(*inputs)
        if self.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
            raise OSError("Windows blocked the input (an admin window or a security prompt may be in front).")

    # --- UI Automation lookups for the safety checks ---------------------
    def element_at(self, x: int, y: int) -> str:
        """Name and type of the control under a point, for the risky-click check."""
        from .uia import _auto

        auto = _auto()
        ctrl = auto.ControlFromPoint(int(x), int(y))
        for _ in range(3):  # icons and text spans often sit inside the named button
            if ctrl is None or (ctrl.Name or "").strip():
                break
            ctrl = ctrl.GetParentControl()
        if ctrl is None:
            return ""
        return f'{ctrl.ControlTypeName.removesuffix("Control")} "{(ctrl.Name or "").strip()[:80]}"'

    def focused_is_password(self) -> bool:
        from .uia import _auto

        try:
            ctrl = _auto().GetFocusedControl()
            return bool(ctrl and ctrl.IsPassword)
        except Exception:  # noqa: BLE001 - if UIA can't tell, the model-side rules still apply
            return False

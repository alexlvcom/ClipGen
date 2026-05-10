"""Native Windows global hotkey backend."""

import ctypes
import logging
from ctypes import wintypes
from queue import Queue
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger('ClipGen')

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID_BASE = 0xC600

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL


class MSG(ctypes.Structure):
    """Windows MSG structure passed through Qt nativeEvent."""

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


VK_BY_NAME = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "pause": 0x13,
    "capslock": 0x14,
    "caps lock": 0x14,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "page up": 0x21,
    "pagedown": 0x22,
    "page down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
}

for i in range(1, 25):
    VK_BY_NAME[f"f{i}"] = 0x70 + i - 1


def _normalize_part(part: str) -> str:
    return part.strip().lower().replace(" ", "")


def parse_hotkey_combination(combination: str) -> Optional[Tuple[int, int]]:
    """Convert a user-visible combination into RegisterHotKey modifiers and vk."""
    if not combination or "+" not in combination:
        return None

    parts = [_normalize_part(part) for part in combination.split("+") if part.strip()]
    if len(parts) < 2:
        return None

    modifiers = MOD_NOREPEAT
    for part in parts[:-1]:
        if part in ("ctrl", "control"):
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
        elif part in ("meta", "win", "windows", "cmd", "super"):
            modifiers |= MOD_WIN
        else:
            return None

    key = parts[-1]
    if len(key) == 1 and "a" <= key <= "z":
        return modifiers, ord(key.upper())
    if len(key) == 1 and "0" <= key <= "9":
        return modifiers, ord(key)
    if key in VK_BY_NAME:
        return modifiers, VK_BY_NAME[key]

    return None


class Win32HotkeyBackend:
    """Registers configured hotkeys with Windows and dispatches WM_HOTKEY."""

    name = "win32"

    def __init__(self, config: Dict[str, Any], event_queue: Queue):
        self.config = config
        self.queue = event_queue
        self.window = None
        self.hwnd: Optional[int] = None
        self.registered: Dict[int, Dict[str, Any]] = {}
        self.last_error = ""

    def set_window(self, window) -> None:
        """Set the Qt window used to receive WM_HOTKEY messages."""
        self.window = window
        self.hwnd = int(window.winId()) if window else None

    def start(self) -> bool:
        """Register all configured hotkeys."""
        if not self.hwnd:
            self.last_error = "No native window handle available"
            logger.warning("Win32 hotkey backend not started: %s", self.last_error)
            return False

        self.stop()
        self.last_error = ""

        registered_count = 0
        for index, hotkey in enumerate(self.config.get("hotkeys", [])):
            combination = hotkey.get("combination", "")
            parsed = parse_hotkey_combination(combination)
            if not parsed:
                logger.warning("Win32 hotkey skipped unsupported combination: %s", combination)
                continue

            modifiers, vk = parsed
            hotkey_id = HOTKEY_ID_BASE + index

            if user32.RegisterHotKey(self.hwnd, hotkey_id, modifiers, vk):
                self.registered[hotkey_id] = hotkey
                registered_count += 1
                logger.info("Win32 hotkey registered: %s -> %s", combination, hotkey.get("name", ""))
                continue

            error_code = ctypes.get_last_error()
            message = (
                f"Failed to register hotkey {combination}. "
                f"It may already be used by another application. Win32 error: {error_code}"
            )
            self.last_error = message
            logger.warning(message)

        if registered_count:
            logger.info("Win32 hotkey backend started with %s registered hotkey(s)", registered_count)
            return True

        if not self.last_error:
            self.last_error = "No supported hotkeys were registered"
        logger.warning("Win32 hotkey backend failed: %s", self.last_error)
        return False

    def stop(self) -> None:
        """Unregister previously registered hotkeys."""
        if not self.hwnd or not self.registered:
            self.registered.clear()
            return

        for hotkey_id, hotkey in list(self.registered.items()):
            if not user32.UnregisterHotKey(self.hwnd, hotkey_id):
                error_code = ctypes.get_last_error()
                logger.warning(
                    "Failed to unregister hotkey %s: Win32 error %s",
                    hotkey.get("combination", hotkey_id),
                    error_code,
                )
        self.registered.clear()
        logger.info("Win32 hotkey backend stopped")

    def restart(self) -> bool:
        """Re-register hotkeys from current config."""
        self.stop()
        return self.start()

    def is_running(self) -> bool:
        """Return whether any hotkeys are currently registered."""
        return bool(self.registered)

    def handle_native_event(self, message) -> bool:
        """Handle a Qt native event. Returns True when consumed."""
        try:
            msg = MSG.from_address(int(message))
        except Exception as e:
            self.last_error = f"Failed to read native message: {e}"
            logger.warning(self.last_error)
            return False

        if msg.message != WM_HOTKEY:
            return False

        hotkey = self.registered.get(int(msg.wParam))
        if not hotkey:
            logger.warning("Received unknown WM_HOTKEY id: %s", int(msg.wParam))
            return True

        logger.info("[%s: %s] Activated via Win32", hotkey.get("combination", ""), hotkey.get("name", ""))
        self.queue.put({
            "action": hotkey.get("name", ""),
            "prompt": hotkey.get("prompt", ""),
        })
        return True

"""Local side of the push channel: types text as unicode key events (SendInput) into the focused window."""
import ctypes
import time
from collections.abc import Callable
from ctypes import wintypes as wt

BATCH, PAUSE = 10, 0.010  # chars per call, seconds between calls: ~910 chars/s, the measured clean limit (50 corrupts)
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 4, 2


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # only here so the union gets the real INPUT size
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("data", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _key(char: str, flags: int) -> INPUT:
    return INPUT(type=1, ki=KEYBDINPUT(0, ord(char), KEYEVENTF_UNICODE | flags, 0, 0))


def type_text(text: str, may_type: Callable[[], bool]):
    """Never types while `may_type` is false: keystrokes must not land in some other window."""
    for i in range(0, len(text), BATCH):
        if not may_type():
            raise ConnectionError("the RDP window lost focus, typing stopped")
        events = [e for ch in text[i:i + BATCH] for e in (_key(ch, 0), _key(ch, KEYEVENTF_KEYUP))]
        if ctypes.windll.user32.SendInput(len(events), (INPUT * len(events))(*events), ctypes.sizeof(INPUT)) != len(events):
            raise OSError("SendInput was blocked")
        time.sleep(PAUSE)

"""Local side of the pull channel: finds, focuses and screenshots the RDP window."""
import ctypes
import sys
import time
from ctypes import wintypes as wt

import mss
import numpy as np

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
ALREADY_EXISTS = 183
SW_MINIMIZE, SW_RESTORE = 6, 9
WM_SYSCOMMAND, SC_MAXIMIZE = 0x0112, 0xF030
GWL_STYLE, WS_CAPTION = -16, 0xC00000
GA_ROOT = 2
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 2, 4


class Window:
    def __init__(self, title: str):
        user32.SetProcessDPIAware()  # real pixels, no Windows scaling
        # One owner of the screen and the keyboard; the handle dies with the process.
        kernel32.CreateMutexW(None, False, "mqs-screen")
        if kernel32.GetLastError() == ALREADY_EXISTS:
            sys.exit("mqs: another mqs session already uses the screen, wait for it to finish")
        self._hwnd = _find(title)
        self._sct = None  # mss keeps per-thread handles: made on first grab, by the capturing thread
        self.focus()

    def focus(self):
        if user32.IsIconic(self._hwnd):
            user32.ShowWindow(self._hwnd, SW_RESTORE)
        self._fullscreen()
        if not self.has_focus():
            user32.keybd_event(0x12, 0, 0, 0)  # an Alt tap lifts the foreground lock
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(self._hwnd)
            time.sleep(0.5)
        if not self.has_focus():
            sys.exit("mqs: the RDP window did not take focus")

    def _fullscreen(self):
        """Windows App goes fullscreen (no title bar, no scrollbars) on its own SC_MAXIMIZE; ShowWindow(SW_MAXIMIZE) does not."""
        def captioned() -> bool:
            return bool(user32.GetWindowLongW(self._hwnd, GWL_STYLE) & WS_CAPTION)

        if captioned():
            user32.PostMessageW(self._hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
            deadline = time.monotonic() + 3
            while captioned() and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(0.5)  # the session resizes after the frame

    def has_focus(self) -> bool:
        return user32.GetForegroundWindow() == self._hwnd

    def grab(self) -> np.ndarray:
        """RGB pixels of the window's client area."""
        self._sct = self._sct or mss.MSS()
        rect, origin = wt.RECT(), wt.POINT(0, 0)
        user32.GetClientRect(self._hwnd, ctypes.byref(rect))
        user32.ClientToScreen(self._hwnd, ctypes.byref(origin))
        shot = self._sct.grab({"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom})
        return np.ascontiguousarray(np.asarray(shot)[:, :, 2::-1])

    def click_center(self):
        """A real click activates the gateway's topmost window where its own focus_force() is refused. Skipped unless
        the RDP window is what sits under the pointer, so the click can never land in another application."""
        rect = wt.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        if user32.GetAncestor(user32.WindowFromPoint(wt.POINT(x, y)), GA_ROOT) != self._hwnd:
            return
        home = wt.POINT()
        user32.GetCursorPos(ctypes.byref(home))
        user32.SetCursorPos(x, y)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.1)  # the RDP client forwards moves and clicks in order: the pointer goes back only after the click
        user32.SetCursorPos(home.x, home.y)

    def minimize(self):
        user32.ShowWindow(self._hwnd, SW_MINIMIZE)  # a minimized window means: done, safe to leave


def _find(title: str) -> int:
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            text = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, text, n + 1)
            if title.lower() in text.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else sys.exit(f"mqs: no visible window with {title!r} in its title (set MQS_WINDOW)")

"""Remote side: a maximized tk window that shows the grid and reports the characters typed into it."""
import _thread
import ctypes
import threading
import time
import tkinter as tk
from collections.abc import Callable

from .grid import CELL

TITLE = "mqs screen"
user32 = ctypes.windll.user32
GA_ROOT = 2  # the top-level window that owns the tk widget window


class Display:
    """All tk calls stay on the display thread. `cols`/`rows` (cells) are known once the constructor returns."""

    def __init__(self):
        self._source: Callable[[], bytes] | None = None
        self._on_char: Callable[[str], None] | None = None
        self._fps = 1.0
        self._ready, self._closed = threading.Event(), threading.Event()
        self._error: BaseException | None = None
        threading.Thread(target=self._guarded, daemon=True).start()
        self._ready.wait()
        if self._error:
            raise self._error

    def start(self, source: Callable[[], bytes], on_char: Callable[[str], None], fps: float):
        """`source` returns the next PPM frame, `on_char` gets every typed character."""
        self._source, self._on_char, self._fps = source, on_char, fps

    def close(self):
        self._closed.set()

    def _guarded(self):
        try:
            self._run()
        except BaseException as e:
            self._error = e
            self._ready.set()

    def _run(self):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # no Windows scaling blur
        root = tk.Tk()
        root.title(TITLE)
        root.configure(bg="black")
        root.state("zoomed")
        root.attributes("-topmost", True)  # nothing may cover the frame
        root.update()
        label = tk.Label(root, bd=0, bg="black")
        label.place(x=0, y=0)
        root.bind("<Key>", lambda e: e.char and self._on_char and self._on_char(e.char))
        root.bind("<Escape>", lambda _: _thread.interrupt_main())  # stops `mqs serve`
        root.protocol("WM_DELETE_WINDOW", _thread.interrupt_main)
        root.focus_force()
        self.cols, self.rows = root.winfo_width() // CELL, root.winfo_height() // CELL
        self._ready.set()
        due = time.perf_counter()
        while not self._closed.is_set():
            if self._source and time.perf_counter() >= due:
                if user32.GetForegroundWindow() == user32.GetAncestor(root.winfo_id(), GA_ROOT):
                    image = tk.PhotoImage(data=self._source(), format="PPM").zoom(CELL)
                else:  # typed keys would go elsewhere: hide the grid so the helper stops typing
                    root.focus_force()
                    image = ""
                label.configure(image=image)
                label.image = image
                due = max(due + 1 / self._fps, time.perf_counter() - 1 / self._fps)
            root.update()  # keys arrive here, so poll often
            time.sleep(0.002)
        root.destroy()

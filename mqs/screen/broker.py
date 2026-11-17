"""Broker over an RDP window, same surface as the MQTT one. The gateway shows frames and reads typed keys,
the helper side screenshots the frames and types its packets. Messages are [topic length][topic][payload]."""
import os
import sys
import threading
import time

from . import grid, keytext
from .link import OVERHEAD, Link, new_epoch

FPS = 5  # measured: every shown frame is seen
FRAME_WINDOW = 16
KEY_MTU = 200  # bytes: ~270 typed chars, a damaged one costs little
KEY_WINDOW = 8
KEY_RTO = 8  # s; the remote may lag behind its typed backlog
DRAIN = 30  # s to get the last messages acked on close
NO_GRID_HINT = 15  # s
GRID_FRESH = 3  # s the grid may be unseen before typing pauses


class ScreenBroker:
    _link: Link

    def __init__(self):
        self._callback = None

    def publish(self, topic: str, payload: bytes):
        name = topic.encode()
        self._link.send(bytes([len(name)]) + name + payload)

    def subscribe(self, topic: str, callback):
        self._callback = callback  # one peer, one stream of messages: every message is for the only subscriber
        self._start()

    def close(self):
        self._link.drain(DRAIN)
        self._link.close()
        self._stop()

    def _deliver(self, message: bytes):
        n = message[0]
        self._callback(message[1:1 + n].decode(), message[1 + n:])


class Gateway(ScreenBroker):
    def __init__(self):
        super().__init__()
        from .display import Display
        self._display = Display()
        self._link = Link(grid.capacity(self._display.cols, self._display.rows) - OVERHEAD, FRAME_WINDOW, 0,
                          self._deliver)
        self._keys = keytext.Decoder(self._link.feed)

    def _start(self):
        cols, rows = self._display.cols, self._display.rows
        self._display.start(lambda: grid.render(self._link.next_packet(force=True), cols, rows), self._keys.feed, FPS)

    def _stop(self):
        self._display.close()


class Client(ScreenBroker):
    def __init__(self, title: str):
        super().__init__()
        from .capture import Window
        self._window = Window(title)
        self._link = Link(KEY_MTU, KEY_WINDOW, KEY_RTO, self._deliver, epoch=new_epoch())
        self._closed = threading.Event()
        self._grid_at = float("-inf")  # when the grid was last seen

    def _start(self):
        for loop in (self._capture, self._type):
            threading.Thread(target=self._guarded, args=(loop,), daemon=True).start()

    def _stop(self):
        self._closed.set()
        self._window.minimize()

    def _capture(self):
        from .scan import decode
        start, warned = time.monotonic(), False
        while not self._closed.is_set():
            now = time.monotonic()
            if frame := decode(self._window.grab()):
                self._grid_at, warned = now, False
                self._link.feed(frame)
            elif not warned and now - max(start, self._grid_at) > NO_GRID_HINT:
                warned = True
                print(f"mqs: no grid on the screen for {NO_GRID_HINT} s, is `mqs serve` running in the RDP session?",
                      file=sys.stderr, flush=True)

    def _type(self):
        from .sendkeys import type_text
        while not self._closed.is_set():
            # The gateway hides the grid when it loses focus: no fresh grid means the keys would land in another window.
            if time.monotonic() - self._grid_at > GRID_FRESH or not (packet := self._link.next_packet()):
                time.sleep(0.02)
                continue
            type_text(keytext.encode(packet), self._window.has_focus)

    def _guarded(self, loop):
        try:
            loop()
        except Exception as e:
            if self._closed.is_set():
                return  # minimizing at close makes an unfinished typing run fail: not an error
            self._window.minimize()
            print(f"fatal: mqs: screen link: {e}", file=sys.stderr, flush=True)
            os._exit(128)

import os
import queue
import struct
import threading
import time

from cryptography.exceptions import InvalidTag

from . import crypto
from .config import Config

HELLO, READY, DATA, STATUS, END, ERROR = range(6)
CHUNK = 64 * 1024
RATE = 400 * 1024  # bytes/s, below the AWS IoT 512 KiB/s per-connection cap
MIN_GAP = 0.02  # s, below the AWS IoT 100 publishes/s per-connection cap
HEARTBEAT = 10  # s; each side treats the peer's frames as its liveness signal
HEADER = struct.Struct(">QB")  # seq, kind; authenticated as AAD together with the direction
NONCE = 12


class Stream:
    """One direction pair of a session: `side` is b"c" (helper) or b"s" (serve)."""

    def __init__(self, broker, cfg: Config, sid: str, side: bytes):
        self._broker = broker
        self._cipher = crypto.session_cipher(cfg.key, sid)
        self._send_dir, self._recv_dir = side, b"s" if side == b"c" else b"c"
        self._send_topic = f"{cfg.base}/{sid}/{side.decode()}"
        self.recv_topic = f"{cfg.base}/{sid}/{self._recv_dir.decode()}"
        self._seq = 0
        self._next_at = 0.0
        self._lock = threading.Lock()
        self._expected = 0
        self._pending: dict[int, tuple[int, bytes]] = {}
        self._inbox: queue.Queue = queue.Queue()

    def send(self, kind: int, payload: bytes = b""):
        with self._lock:
            # Pacing, not a bucket: idle time must not turn into a burst over the cap.
            time.sleep(max(0.0, self._next_at - time.monotonic()))
            header = HEADER.pack(self._seq, kind)
            # Random nonce: a retried seq or a session replayed after restart never reuses one.
            nonce = os.urandom(NONCE)
            frame = header + nonce + self._cipher.encrypt(nonce, payload, self._send_dir + header)
            self._next_at = time.monotonic() + max(MIN_GAP, len(frame) / RATE)
            self._broker.publish(self._send_topic, frame)
            self._seq += 1

    def feed(self, frame: bytes):
        """Runs on the MQTT thread: must never block. Drops forged, duplicate and stale frames."""
        if len(frame) < HEADER.size + NONCE:
            return
        seq, kind = HEADER.unpack_from(frame)
        if seq < self._expected or seq in self._pending:
            return
        header, nonce, body = frame[:HEADER.size], frame[HEADER.size:HEADER.size + NONCE], frame[HEADER.size + NONCE:]
        try:
            payload = self._cipher.decrypt(nonce, body, self._recv_dir + header)
        except InvalidTag:
            return
        self._pending[seq] = (kind, payload)
        while self._expected in self._pending:
            self._inbox.put(self._pending.pop(self._expected))
            self._expected += 1

    def recv(self, timeout: float) -> tuple[int, bytes]:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def abort(self, message: str):
        """Ends the local recv loop with ERROR, regardless of what the peer sends."""
        self._inbox.put((ERROR, message.encode()))

    def start_heartbeat(self) -> threading.Event:
        """Sends empty STATUS frames until the returned event is set."""
        stop = threading.Event()

        def beat():
            try:
                while not stop.wait(HEARTBEAT):
                    self.send(STATUS)
            except Exception:
                pass  # broker gone: the peer's idle timeout reports it

        threading.Thread(target=beat, daemon=True).start()
        return stop

"""Reliable ordered messages over a lossy packet channel: selective acks, least-recently-sent resend."""
import struct
import threading
import time
import zlib
from collections.abc import Callable

HEADER = struct.Struct(">IIIHH")  # epoch, seq, ack base (next expected seq), ack bitmap, payload length
CRC = struct.Struct(">I")
OVERHEAD = HEADER.size + CRC.size
MASK_BITS = 16  # bit i of the bitmap: seq ack_base + 1 + i arrived
BUFFER = 1 << 20  # queued bytes before send() blocks
LENGTH = struct.Struct(">I")
MAX_PAYLOAD = 0xFFFF  # the header's length field
EPOCHS = 1 << 32  # epochs wrap: a greater one is up to half the range ahead
SESSION_GAP = 300  # s of silence after which the passive end takes any epoch as a new session


def new_epoch() -> int:
    """Milliseconds, so a quick retry still counts as newer."""
    return time.time_ns() // 1_000_000 % EPOCHS



class Link:
    """One end of a duplex link. The transport calls next_packet() to emit and feed() with what it received.

    Data packets carry the ack of the reverse channel, so an ack-only packet (empty payload) is the idle frame.
    `window` and `rto` shape only this end's sending: rto 0 re-emits the oldest unacked packet on every call.
    The active end picks a new_epoch() per session; the passive end (epoch=None) adopts a newer one and starts
    over, ignoring late packets of older sessions: a fresh helper never meets old state.
    """

    def __init__(self, mtu: int, window: int, rto: float, on_message: Callable[[bytes], None],
                 epoch: int | None = None, clock: Callable[[], float] = time.monotonic):
        assert window <= MASK_BITS + 1
        self.mtu, self.window, self.rto = min(mtu, MAX_PAYLOAD), window, rto
        self._on_message, self._clock = on_message, clock
        self._passive = epoch is None
        self._epoch = epoch
        self._heard = float("-inf")  # last valid packet of the current epoch
        self._cv = threading.Condition()
        self._closed = False
        self._reset()

    def _reset(self):
        self._out = bytearray()
        self._unacked: dict[int, list] = {}  # seq -> [payload, last sent]
        self._next_seq = 0
        self._expected = 0
        self._pending: dict[int, bytes] = {}
        self._inbound = bytearray()
        self._ack_dirty = False

    def send(self, message: bytes):
        """Queues a message; blocks while the queue is full (backpressure for the slow channel)."""
        with self._cv:
            self._cv.wait_for(lambda: self._closed or len(self._out) < BUFFER)
            if self._closed:
                raise ConnectionError("link closed")
            self._out += LENGTH.pack(len(message)) + message

    def next_packet(self, force: bool = False) -> bytes | None:
        """Oldest due data packet, else an ack-only one when `force` or the ack changed, else None."""
        with self._cv:
            # The window spans seqs, not packet count: the peer only accepts seqs near its first gap.
            while self._out and self._next_seq - min(self._unacked, default=self._next_seq) < self.window:
                self._unacked[self._next_seq] = [bytes(self._out[:self.mtu]), float("-inf")]
                del self._out[:self.mtu]
                self._next_seq += 1
                self._cv.notify_all()
            now = self._clock()
            due = [s for s, (_, sent) in self._unacked.items() if now - sent >= self.rto]
            if due:
                seq = min(due, key=lambda s: self._unacked[s][1])
                self._unacked[seq][1] = now
                payload = self._unacked[seq][0]
            elif force or self._ack_dirty:
                seq, payload = 0, b""
            else:
                return None
            self._ack_dirty = False
            return self._encode(seq, payload)

    def feed(self, packet: bytes):
        """Garbage is dropped silently: a packet is valid only with a matching CRC."""
        if len(packet) < OVERHEAD:
            return
        epoch, seq, base, mask, n = HEADER.unpack_from(packet)
        end = HEADER.size + n
        if len(packet) < end + CRC.size or CRC.unpack_from(packet, end)[0] != zlib.crc32(packet[:end]):
            return
        with self._cv:
            now = self._clock()
            if self._passive and now - self._heard > SESSION_GAP:
                self._epoch = None  # long silence: the old session is over, a plain compare would fail across wraparound
            if self._passive and (self._epoch is None or 0 < (epoch - self._epoch) % EPOCHS < EPOCHS // 2):
                self._epoch = epoch
                self._reset()
            elif epoch != self._epoch:
                return  # a late packet or echo of an older session
            self._heard = now
            for s in [s for s in self._unacked if s < base or (s > base and mask >> (s - base - 1) & 1)]:
                del self._unacked[s]
                self._cv.notify_all()
            if n:
                self._ack_dirty = True  # a duplicate means our ack got lost: repeat it
                self._accept(seq, packet[HEADER.size:end])
            messages = self._take_messages()
        for message in messages:
            self._on_message(message)

    def drain(self, timeout: float) -> bool:
        """Waits until everything sent is acked; the peer's acks arrive through feed()."""
        with self._cv:
            return self._cv.wait_for(lambda: not self._out and not self._unacked, timeout)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _encode(self, seq: int, payload: bytes) -> bytes:
        mask = sum(1 << i for i in range(MASK_BITS) if self._expected + 1 + i in self._pending)
        head = HEADER.pack(self._epoch or 0, seq, self._expected, mask, len(payload)) + payload
        return head + CRC.pack(zlib.crc32(head))

    def _accept(self, seq: int, payload: bytes):
        if self._expected <= seq <= self._expected + MASK_BITS:
            self._pending.setdefault(seq, payload)
            while self._expected in self._pending:
                self._inbound += self._pending.pop(self._expected)
                self._expected += 1

    def _take_messages(self) -> list[bytes]:
        messages = []
        while len(self._inbound) >= LENGTH.size:
            n = LENGTH.unpack_from(self._inbound)[0]
            if len(self._inbound) < LENGTH.size + n:
                break
            messages.append(bytes(self._inbound[LENGTH.size:LENGTH.size + n]))
            del self._inbound[:LENGTH.size + n]
        return messages

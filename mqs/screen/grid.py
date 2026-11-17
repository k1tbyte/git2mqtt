"""Remote side of the pull channel: frame bytes -> grid of 8 pure colors, as a PPM with one pixel per cell."""
import hashlib
from functools import cache

PAL = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 0), (0, 255, 255), (255, 0, 255)]
BLACK, WHITE = 0, 1
RING = 2  # per side: a white edge cell, then a black one; the scanner finds the grid by them
BITS = 3  # per cell
CELL = 4  # real pixels per cell: the measured 8-color limit over RDP

_TABLES = [bytes(PAL[i - 48][ch] if 48 <= i < 56 else 0 for i in range(256)) for ch in range(3)]  # octal digit -> channel


@cache
def _mask(n: int) -> int:
    return int.from_bytes(hashlib.shake_128(b"mqs-grid").digest(n), "big")


def whiten(frame: bytes) -> bytes:
    """XOR with a fixed stream (its own inverse): zero padding must not become a black screen with no rings to find."""
    return (int.from_bytes(frame, "big") ^ _mask(len(frame))).to_bytes(len(frame), "big")


def capacity(cols: int, rows: int) -> int:
    return (cols - 2 * RING) * (rows - 2 * RING) * BITS // 8


def render(frame: bytes, cols: int, rows: int) -> bytes:
    """PPM of the frame, zero-padded to capacity."""
    n, cap = (cols - 2 * RING) * (rows - 2 * RING), capacity(cols, rows)
    if len(frame) > cap:
        raise ValueError(f"frame of {len(frame)} bytes does not fit {cap}")
    digits = format(int.from_bytes(whiten(frame.ljust(cap, b"\0")), "big") << (n * BITS - 8 * cap), f"0{n}o").encode()
    rgb = bytearray(3 * n)
    for ch in range(3):
        rgb[ch::3] = digits.translate(_TABLES[ch])
    white, black = bytes(PAL[WHITE]), bytes(PAL[BLACK])
    ring = white + black * (cols - 2) + white
    step = 3 * (cols - 2 * RING)
    rows_out = [white * cols, ring, *(white + black + rgb[i:i + step] + black + white for i in range(0, 3 * n, step)),
                ring, white * cols]
    return b"P6 %d %d 255\n" % (cols, rows) + b"".join(rows_out)

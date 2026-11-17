"""Text form of a packet on the key channel: base64url ended by '.', the alphabet the remote receives cleanly."""
import base64
import binascii
from collections.abc import Callable

END = "."


def encode(packet: bytes) -> str:
    return base64.urlsafe_b64encode(packet).decode().rstrip("=") + END


class Decoder:
    """Turns typed characters back into packets; a damaged one is passed on and rejected by the link's CRC."""

    def __init__(self, on_packet: Callable[[bytes], None]):
        self._on_packet = on_packet
        self._chars: list[str] = []

    def feed(self, text: str):
        for ch in text:
            if ch != END:
                self._chars.append(ch)
                continue
            body, self._chars = "".join(self._chars), []
            try:
                self._on_packet(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            except (binascii.Error, ValueError):
                pass

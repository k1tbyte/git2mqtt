import base64
import binascii
import hashlib
import secrets
import sys

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


def keygen() -> str:
    raw = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def parse_key(text: str) -> bytes:
    raw = text.replace("-", "").replace(" ", "").upper()
    try:
        key = base64.b32decode(raw + "=" * (-len(raw) % 8))
    except binascii.Error:
        key = b""
    if len(key) != 16:
        sys.exit("mqs: malformed key, expected the output of `mqs keygen`")
    return key


def channel(key: bytes) -> str:
    """Topic segment shared by both sides; reveals nothing about the key."""
    return hashlib.blake2b(key, digest_size=8, person=b"mqs-chan").hexdigest()


def session_cipher(key: bytes, sid: str) -> ChaCha20Poly1305:
    return ChaCha20Poly1305(hashlib.blake2b(sid.encode(), key=key, digest_size=32).digest())

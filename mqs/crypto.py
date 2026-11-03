import base64
import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


def keygen() -> str:
    raw = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def derive_key(text: str) -> bytes:
    """Any string works, as in the single-file version; `mqs keygen` makes a strong one."""
    return hashlib.blake2b(text.encode(), digest_size=32, person=b"mqs-key").digest()


def channel(key: bytes) -> str:
    """Topic segment shared by both sides; reveals nothing about the key."""
    return hashlib.blake2b(key, digest_size=8, person=b"mqs-chan").hexdigest()


def session_cipher(key: bytes, sid: str) -> ChaCha20Poly1305:
    return ChaCha20Poly1305(hashlib.blake2b(sid.encode(), key=key, digest_size=32).digest())

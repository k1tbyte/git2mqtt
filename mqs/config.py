import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import crypto, envfile


@dataclass(frozen=True)
class Config:
    key: bytes
    endpoint: str
    region: str
    topic: str
    profile: str
    upstream: str

    @property
    def base(self) -> str:
        return f"{self.topic}/{crypto.channel(self.key)}"


def load_dotenv():
    """Settings may sit in a .env next to the bundle; real env vars win."""
    for name, value in envfile.read(envfile.app_dir() / ".env").items():
        os.environ.setdefault(name, value)


def load(server: bool = False) -> Config:
    """Reads MQS_* from env/.env, identical on both sides; MQS_UPSTREAM is server-only."""
    load_dotenv()

    def need(name: str) -> str:
        return os.environ.get(name) or sys.exit(f"mqs: set {name} in .env next to the mqs file")

    endpoint = need("MQS_ENDPOINT")
    return Config(
        key=crypto.derive_key(need("MQS_KEY")),
        endpoint=endpoint,
        region=os.environ.get("MQS_REGION") or _region(endpoint),
        topic=os.environ.get("MQS_TOPIC") or "mqs",  # both sides must match; override to blend into existing IoT traffic
        profile=os.environ.get("MQS_PROFILE", ""),
        upstream=need("MQS_UPSTREAM") if server else "",
    )


def _region(endpoint: str) -> str:
    """AWS IoT endpoints look like <id>-ats.iot.<region>.amazonaws.com; empty for the tcp:// test broker."""
    return endpoint.split(".iot.")[1].split(".")[0] if ".iot." in endpoint else ""

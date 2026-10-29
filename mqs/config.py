import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import crypto

DEFAULTS = {
    "mqs.topic": "mqs",  # both sides must match; override to blend into existing IoT traffic
    "mqs.profile": "",
}


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
    """AWS creds may sit in a .env next to the bundle; real env vars win."""
    path = Path(sys.argv[0]).resolve().parent / ".env"  # resolve: unix installs reach it via a symlink
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def load(server: bool = False) -> Config:
    """Reads the mqs.* section, identical on both sides; upstream is server-only."""
    load_dotenv()
    out = subprocess.run(["git", "config", "--get-regexp", r"^mqs\."], capture_output=True, encoding="utf-8").stdout
    values = {**DEFAULTS, **dict(line.partition(" ")[::2] for line in out.splitlines())}

    def need(name: str) -> str:
        if not values.get(name):
            sys.exit(f"mqs: missing setting, run: git config --global {name} <value>")
        return values[name]

    endpoint = need("mqs.endpoint")
    return Config(
        key=crypto.parse_key(need("mqs.key")),
        endpoint=endpoint,
        region=values.get("mqs.region") or _region(endpoint),
        topic=values["mqs.topic"],
        profile=values["mqs.profile"],
        upstream=need("mqs.upstream") if server else "",
    )


def _region(endpoint: str) -> str:
    """AWS IoT endpoints look like <id>-ats.iot.<region>.amazonaws.com; empty for the tcp:// test broker."""
    return endpoint.split(".iot.")[1].split(".")[0] if ".iot." in endpoint else ""

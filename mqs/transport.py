"""Picks the broker for MQS_ENDPOINT: `screen://` is the RDP window, anything else is MQTT."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # install imports SCREEN before the deps (cryptography, via config) exist
    from .config import Config

SCREEN = "screen://"


def connect(cfg: Config, server: bool):
    if cfg.endpoint == SCREEN:
        from .screen.broker import Client, Gateway  # imports stay lazy: each side has other deps
        return Gateway() if server else Client(cfg.window or _no_window())
    from .broker import Broker
    return Broker(cfg)


def _no_window():
    raise SystemExit("mqs: set MQS_WINDOW (part of the RDP window title) in .env")

import os
import sys

import pytest

from mqs import config


def test_dotenv_next_to_bundle_fills_only_missing_vars(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('﻿A_KEY=one\n# B_KEY=skip\nC_KEY="two"\nSET_KEY=new\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "git-remote-mqs")])
    for name in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SET_KEY", "old")

    config.load_dotenv()

    assert (os.environ["A_KEY"], os.environ["C_KEY"], os.environ["SET_KEY"]) == ("one", "two", "old")
    assert "B_KEY" not in os.environ


def test_load_accepts_single_file_version_env_with_hex_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "MQS_ENDPOINT=a1b2c3-ats.iot.eu-west-1.amazonaws.com\nMQS_TOPIC=team\nMQS_KEY=" + "0f" * 16 + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "mqs")])
    monkeypatch.setattr(os, "environ", {})  # load_dotenv writes into it, must not leak into other tests

    cfg = config.load()

    assert (cfg.topic, cfg.region) == ("team", "eu-west-1")
    with pytest.raises(SystemExit, match="MQS_UPSTREAM"):
        config.load(server=True)

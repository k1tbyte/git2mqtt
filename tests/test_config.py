import os
import sys

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

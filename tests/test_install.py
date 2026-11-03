import pytest

from mqs import install


def test_complete_env_asks_only_for_missing_settings(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MQS_ENDPOINT=e\nMQS_TOPIC=t\nMQS_KEY=k\nAWS_ACCESS_KEY_ID=a\nAWS_SECRET_ACCESS_KEY=s", encoding="utf-8")
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "https://git.example.com/")

    result = install.complete_env(tmp_path, tmp_path / "python", tmp_path / "mqs")

    assert len(asked) == 1 and asked[0].startswith("MQS_UPSTREAM")
    assert result["MQS_UPSTREAM"] == "https://git.example.com/" and result["MQS_TOPIC"] == "t"
    assert env.read_text(encoding="utf-8").endswith("AWS_SECRET_ACCESS_KEY=s\nMQS_UPSTREAM=https://git.example.com/\n")


def test_complete_env_skips_aws_keys_with_profile(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MQS_ENDPOINT=e\nMQS_UPSTREAM=u\nMQS_KEY=k\nMQS_PROFILE=work\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail(f"unexpected prompt: {prompt}"))

    install.complete_env(tmp_path, tmp_path / "python", tmp_path / "mqs")


def test_complete_env_replaces_blank_placeholder(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MQS_ENDPOINT=e\nMQS_UPSTREAM=\nMQS_KEY=k\nAWS_ACCESS_KEY_ID=a\nAWS_SECRET_ACCESS_KEY=s\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: "https://git.example.com/")

    install.complete_env(tmp_path, tmp_path / "python", tmp_path / "mqs")

    assert env.read_text(encoding="utf-8").count("MQS_UPSTREAM") == 1

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from amqtt.broker import Broker

SCRIPT = Path(__file__).parents[1] / "mqs.py"
IDENTITY = {
    f"GIT_{role}_{field}": value
    for role in ("AUTHOR", "COMMITTER")
    for field, value in (("NAME", "Tester"), ("EMAIL", "tester@example.com"))
}


def git(repo: Path, *args) -> str:
    proc = subprocess.run(["git", *map(str, args)], cwd=repo, capture_output=True, text=True, env={**os.environ, **IDENTITY})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def commit(repo: Path, name: str, size: int = 10) -> str:
    (repo / f"{name}.txt").write_bytes(secrets.token_bytes(size))  # random: git cannot compress it below the chunk size
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def endpoint():
    port = free_port()
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    }

    async def serve():
        await Broker(config).start()
        await asyncio.Event().wait()

    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return f"tcp://127.0.0.1:{port}"
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("broker did not start")


class Pair:
    """Two repos sharing one initial commit; the sender gets ahead, the receiver stays behind."""

    def __init__(self, tmp: Path, endpoint: str):
        self.sender, self.receiver = tmp / "sender", tmp / "receiver"
        self.sender.mkdir()
        git(self.sender, "init", "-q", "-b", "main")
        self.base = commit(self.sender, "base")
        git(tmp, "clone", "-q", self.sender, self.receiver)
        self.env = {**os.environ, "MQS_ENDPOINT": endpoint, "MQS_TOPIC": "mqs", "MQS_KEY": secrets.token_hex(8)}

    def serve(self, answer: str = "y") -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, SCRIPT, "--serve", "--idle", "1", str(self.sender)],
                                cwd=self.sender.parent, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout.readline().startswith("waiting")  # subscribed, safe to ask
        proc.stdin.write(f"{answer}\n")
        proc.stdin.flush()
        return proc

    def start_pull(self) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, SCRIPT, "--idle", "1"], cwd=self.receiver, env=self.env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def pull(self, answer: str | None = "n") -> tuple[str, int]:
        """`answer` goes to the overwrite question, which only comes up when history was rewritten; None closes stdin."""
        proc = self.start_pull()
        return proc.communicate(None if answer is None else f"{answer}\n", timeout=120)[0], proc.returncode


@pytest.fixture
def pair(tmp_path, endpoint):
    return Pair(tmp_path, endpoint)


def test_fast_forwards_checked_out_branch(pair):
    server = pair.serve()
    head = commit(pair.sender, "one")
    out, code = pair.pull()
    assert code == 0, out
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "HEAD") == head
    assert (pair.receiver / "one.txt").exists()


def test_multi_chunk_transfer(pair):
    server = pair.serve()
    head = commit(pair.sender, "big", size=300 * 1024)
    out, code = pair.pull()
    assert code == 0, out
    assert "sent 5/5" in server.communicate(timeout=30)[0]
    assert git(pair.receiver, "rev-parse", "HEAD") == head


def test_updates_ref_when_branch_is_not_checked_out(pair):
    git(pair.receiver, "checkout", "-q", "-b", "other")
    server = pair.serve()
    head = commit(pair.sender, "one")
    assert pair.pull()[1] == 0
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "main") == head
    assert git(pair.receiver, "rev-parse", "HEAD") == pair.base  # untouched


def test_empty_receiver_gets_the_whole_branch(pair, tmp_path):
    pair.receiver = tmp_path / "empty"
    pair.receiver.mkdir()
    git(pair.receiver, "init", "-q", "-b", "main")
    server = pair.serve()
    head = commit(pair.sender, "one")
    out, code = pair.pull()
    assert code == 0, out
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "HEAD") == head


def test_branch_name_shared_with_a_tag(pair):
    git(pair.sender, "tag", "main")
    server = pair.serve()
    head = commit(pair.sender, "one")
    assert pair.pull()[1] == 0
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "HEAD") == head


def test_receiver_may_start_first(pair):
    receiver = pair.start_pull()
    time.sleep(1)  # its first request goes out before anyone listens
    head = commit(pair.sender, "one")
    server = pair.serve()
    out = receiver.communicate(timeout=60)[0]
    assert receiver.returncode == 0, out
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "HEAD") == head


def test_path_that_is_not_a_repo_fails(pair, tmp_path):
    proc = subprocess.run([sys.executable, SCRIPT, str(tmp_path / "missing")], env=pair.env, capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 1
    assert "is not a git repo" in proc.stderr


def test_declined_sends_nothing(pair):
    server = pair.serve("n")
    commit(pair.sender, "one")
    out, code = pair.pull()
    assert code == 1
    assert "declined" in out
    assert server.wait(30) == 1
    assert git(pair.receiver, "rev-parse", "HEAD") == pair.base


def test_nothing_new_reports_error(pair):
    server = pair.serve()
    out, code = pair.pull()
    assert code == 1
    assert server.wait(30) == 1


def test_diverged_receiver_keeps_its_commits_when_declined(pair):
    theirs = commit(pair.receiver, "theirs")
    server = pair.serve()
    commit(pair.sender, "mine")
    out, code = pair.pull("n")
    assert code == 1
    assert "declined" in out
    assert server.wait(30) == 1
    assert git(pair.receiver, "rev-parse", "HEAD") == theirs


def test_closed_stdin_counts_as_declined(pair):
    theirs = commit(pair.receiver, "theirs")
    server = pair.serve()
    commit(pair.sender, "mine")
    out, code = pair.pull(None)
    assert code == 1
    assert "Traceback" not in out
    assert server.wait(30) == 1
    assert git(pair.receiver, "rev-parse", "HEAD") == theirs


def test_staged_changes_block_an_overwrite(pair):
    theirs = commit(pair.receiver, "theirs")
    (pair.receiver / "wip.txt").write_text("wip")
    git(pair.receiver, "add", "wip.txt")
    server = pair.serve()
    commit(pair.sender, "mine")
    out, code = pair.pull("y")
    assert code == 1
    assert "staged" in out
    assert server.wait(30) == 1
    assert git(pair.receiver, "rev-parse", "HEAD") == theirs
    assert git(pair.receiver, "diff", "--cached", "--name-only") == "wip.txt"


def test_bare_receiver_moves_its_default_branch(pair, tmp_path):
    pair.receiver = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", pair.sender, pair.receiver)
    server = pair.serve()
    head = commit(pair.sender, "one")
    out, code = pair.pull()
    assert code == 0, out
    assert server.wait(30) == 0
    assert git(pair.receiver, "rev-parse", "main") == head


@pytest.mark.parametrize("prune", [False, True], ids=["old-tip-still-known", "old-tip-gone"])
def test_rewritten_history_overwrites_from_newest_shared_commit(pair, prune):
    server = pair.serve()
    commit(pair.sender, "big", size=300 * 1024)
    assert pair.pull()[1] == 0
    assert server.wait(30) == 0
    git(pair.sender, "commit", "-q", "--amend", "-m", "rewritten")  # what a rebase does to the receiver's tip
    new = commit(pair.sender, "after")
    if prune:
        git(pair.sender, "reflog", "expire", "--expire=now", "--all")
        git(pair.sender, "gc", "-q", "--prune=now")
    server = pair.serve()
    out, code = pair.pull("y")
    sent = server.communicate(timeout=30)[0]
    assert code == 0, out
    assert git(pair.receiver, "rev-parse", "HEAD") == new
    assert "whole branch" not in sent  # found a shared commit, even with the old tip pruned



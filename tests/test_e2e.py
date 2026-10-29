import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from amqtt.broker import Broker

import bundle
from mqs import crypto

TIMEOUT = 120
IDENTITY = {
    f"GIT_{role}_{field}": value
    for role in ("AUTHOR", "COMMITTER")
    for field, value in (("NAME", "Tester"), ("EMAIL", "tester@example.com"))
}


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, **kwargs)


def git_env(tmp: Path, name: str, **extra) -> dict:
    return {**os.environ, **IDENTITY, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(tmp / f"{name}.gitconfig"), **extra}


class Sandbox:
    """Upstream bare repo, a non-tunnelled seed clone that plays 'someone else', and the user's existing local clone."""

    def __init__(self, tmp: Path, helper: Path):
        self.tmp = tmp
        self.helper = helper
        self.base = (tmp / "vcs").as_uri() + "/"
        self.upstream = tmp / "vcs" / "team" / "app.git"
        self.seed = tmp / "seed"
        self.local = tmp / "local"
        self.plain_env = git_env(tmp, "plain")
        # The bundle's sh preamble must find this venv's python, git must find the bundle.
        path = os.pathsep.join([str(helper.parent), str(Path(sys.executable).parent), os.environ["PATH"]])
        self.client_env = git_env(tmp, "client", PATH=path)
        self.server_log: list[str] = []
        self.git("init", "--quiet", "--bare", "-b", "main", self.upstream, cwd=tmp)
        self.git("clone", "--quiet", self.upstream, self.seed, cwd=tmp)
        self.commit(self.seed, "one")
        self.commit(self.seed, "two")
        self.git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        self.git("clone", "--quiet", self.base + "team/app.git", self.local, cwd=tmp)

    def git(self, *args, cwd, client=False) -> str:
        args = [str(a) for a in args]
        proc = run(["git", *args], cwd=cwd, env=self.client_env if client else self.plain_env)
        assert proc.returncode == 0, (
            f"git {' '.join(args)} exited {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}\n"
            f"server log:\n" + "\n".join(self.server_log)
        )
        return proc.stdout.strip()

    def commit(self, repo: Path, name: str) -> str:
        (repo / f"{name}.txt").write_text(name)
        self.git("add", ".", cwd=repo)
        self.git("commit", "--quiet", "-m", name, cwd=repo)
        return self.git("rev-parse", "HEAD", cwd=repo)

    def push_seed(self) -> str:
        sha = self.commit(self.seed, "pushed-by-someone-else")
        self.git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        return sha

    def upstream_refs(self) -> dict[str, str]:
        out = self.git("for-each-ref", "--format=%(refname) %(objectname)", cwd=self.upstream)
        return dict(line.rsplit(" ", 1) for line in out.splitlines())

    def write_config(self, name: str, entries: dict[str, str]):
        for key, value in entries.items():
            self.git("config", "--file", self.tmp / f"{name}.gitconfig", key, value, cwd=self.tmp)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"broker did not start listening on port {port}")


def stop(proc: subprocess.Popen):
    if sys.platform == "win32":
        run(["taskkill", "/F", "/T", "/PID", str(proc.pid)])
    else:
        proc.kill()
    proc.wait()


@pytest.fixture(scope="module")
def broker_port():
    port = free_port()
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    }

    async def main():
        await Broker(config).start()
        await asyncio.Event().wait()

    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    wait_for_port(port)
    return port


@pytest.fixture(scope="module")
def helper(tmp_path_factory):
    return bundle.build(tmp_path_factory.mktemp("bin") / "git-remote-mqs")


@pytest.fixture
def sandbox(tmp_path, helper):
    return Sandbox(tmp_path, helper)


@pytest.fixture
def served(sandbox, broker_port):
    key = crypto.keygen()
    endpoint = f"tcp://127.0.0.1:{broker_port}"
    home = sandbox.tmp / "serverhome"
    home.mkdir()
    sandbox.write_config("server", {
        "mqs.endpoint": endpoint, "mqs.key": key, "mqs.upstream": sandbox.base,
        "url.mqs://.insteadOf": sandbox.base,  # gateway also has the rewrite: mirror must still reach upstream direct
        "core.hooksPath": str(sandbox.tmp / "global-hooks"),  # must not bypass the mirror's forwarding hook
    })
    sandbox.write_config("client", {
        "mqs.endpoint": endpoint, "mqs.key": key, "url.mqs://.insteadOf": sandbox.base,
    })
    proc = subprocess.Popen(
        [sys.executable, sandbox.helper, "serve"],
        cwd=home, env=git_env(sandbox.tmp, "server", HOME=str(home), USERPROFILE=str(home)),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
    )
    ready = threading.Event()

    def pump():
        for line in proc.stdout:
            sandbox.server_log.append(line.rstrip())
            if line.startswith("serving"):
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    try:
        assert ready.wait(60), "mqs serve did not start:\n" + "\n".join(sandbox.server_log)
        yield sandbox
    finally:
        stop(proc)


def test_fetch_gets_new_upstream_commit(served):
    pushed = served.push_seed()

    served.git("fetch", "origin", cwd=served.local, client=True)

    assert served.git("rev-parse", "origin/main", cwd=served.local) == pushed
    assert served.upstream_refs()["refs/heads/main"] == pushed


def test_push_updates_upstream_main(served):
    head = served.commit(served.local, "local")

    served.git("push", "origin", "HEAD:main", cwd=served.local, client=True)

    assert served.upstream_refs()["refs/heads/main"] == head


def test_push_of_several_refs(served):
    head = served.commit(served.local, "local")

    names = ["refs/heads/f1", "refs/heads/фича", "refs/heads/nb sp"]
    served.git("push", "origin", *(f"HEAD:{name}" for name in names), cwd=served.local, client=True)

    refs = served.upstream_refs()
    assert [refs[name] for name in names] == [head] * len(names)


def test_delete_removes_upstream_branch(served):
    served.git("push", "origin", "HEAD:refs/heads/f1", cwd=served.local, client=True)
    assert "refs/heads/f1" in served.upstream_refs()

    served.git("push", "origin", "--delete", "f1", cwd=served.local, client=True)

    assert "refs/heads/f1" not in served.upstream_refs()


def test_serve_exits_when_idle(sandbox, broker_port):
    sandbox.write_config("server", {
        "mqs.endpoint": f"tcp://127.0.0.1:{broker_port}", "mqs.key": crypto.keygen(), "mqs.upstream": sandbox.base,
    })

    proc = run([sys.executable, sandbox.helper, "serve", "--idle", "0.02"], env=git_env(sandbox.tmp, "server"))

    assert proc.returncode == 0 and "closing connection" in proc.stdout, proc.stdout + proc.stderr


def test_forward_rejects_stale_lease(sandbox):
    mirror = sandbox.tmp / "mirror.git"
    sandbox.git("clone", "--quiet", "--bare", sandbox.upstream, mirror, cwd=sandbox.tmp)
    old = sandbox.git("rev-parse", "refs/heads/main", cwd=mirror)
    new = sandbox.git("commit-tree", f"{old}^{{tree}}", "-p", old, "-m", "mine", cwd=mirror)
    advanced = sandbox.push_seed()

    proc = run(
        [sys.executable, "-m", "mqs.forward"], cwd=mirror, input=f"{old} {new} refs/heads/main\n",
        env={**sandbox.plain_env, "GIT_DIR": str(mirror), "LC_ALL": "C"},
    )

    assert proc.returncode != 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "stale info" in proc.stderr, proc.stderr
    assert sandbox.upstream_refs()["refs/heads/main"] == advanced

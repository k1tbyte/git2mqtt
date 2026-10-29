"""Bare mirrors of upstream repos on the gateway; pushes go upstream through a pre-receive hook."""
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / ".mqs" / "mirrors"
PKG_ROOT = Path(__file__).absolute().parents[1]  # project dir or the bundle file itself
PATH_RE = re.compile(r"[\w.-]+(/[\w.-]+)*")
REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")  # skips server-side refs like merge-requests


def sync(upstream: str, path: str) -> str:
    """Clones or refreshes the mirror so the advertised refs match upstream right now."""
    if not PATH_RE.fullmatch(path) or ".." in path.split("/"):
        raise ValueError(f"bad repo path {path!r}")
    # Keyed by upstream too, so a changed mqs.upstream never reuses another server's mirror.
    repo = ROOT / hashlib.sha1(upstream.encode()).hexdigest()[:8] / path
    url = upstream + path
    if _is_repo(repo):
        _git(*direct(url), "-C", str(repo), "fetch", "--quiet", "--prune", "origin")
    else:
        shutil.rmtree(repo, ignore_errors=True)  # drop a half-cloned leftover that otherwise poisons every fetch
        _git(*direct(url), "clone", "--bare", "--quiet", url, str(repo))
        for spec in REFSPECS:
            _git("-C", str(repo), "config", "--add", "remote.origin.fetch", spec)
        # A global core.hooksPath would silently skip forwarding.
        _git("-C", str(repo), "config", "core.hooksPath", (repo / "hooks").as_posix())
    hook = repo / "hooks" / "pre-receive"
    hook.write_text(
        f'#!/bin/sh\nPYTHONPATH="{PKG_ROOT.as_posix()}" exec "{Path(sys.executable).as_posix()}" -m mqs.forward\n',
        encoding="utf-8", newline="\n",  # sh wants LF; install paths may be Cyrillic
    )
    hook.chmod(0o755)
    return str(repo)


def direct(url: str) -> tuple[str, str]:
    """Identity rewrite for this exact URL; its longer prefix beats a url.mqs://.insteadOf on this box,
    so the mirror reaches upstream directly instead of looping back through mqs://."""
    return "-c", f"url.{url}.insteadOf={url}"


def _is_repo(repo: Path) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-bare-repository"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _git(*args: str):
    proc = subprocess.run(["git", *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip())

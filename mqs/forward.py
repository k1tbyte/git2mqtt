"""pre-receive hook: upstream decides; the lease keeps a stale mirror from overwriting anyone's work."""
import os
import subprocess
import sys

from .mirror import direct


def main():
    url = subprocess.run(["git", "config", "remote.origin.url"], capture_output=True, encoding="utf-8",
                         check=True).stdout.strip()
    leases, refspecs = [], []
    sys.stdin.reconfigure(encoding="utf-8")  # git writes ref names as UTF-8, not in the Windows code page
    for line in sys.stdin:
        old, new, ref = line.rstrip("\n").split(" ", 2)  # refs may contain Unicode spaces
        leases.append(f"--force-with-lease={ref}:{'' if _zero(old) else old}")
        refspecs.append(f":{ref}" if _zero(new) else f"{new}:{ref}")
    # The hook verdict is all-or-nothing, so upstream must be too.
    atomic = ["--atomic"] if len(refspecs) > 1 else []
    # A local (file://) upstream would inherit the quarantine flag and refuse ref updates.
    env = {k: v for k, v in os.environ.items() if k != "GIT_QUARANTINE_PATH"}
    # direct(): a url.mqs://.insteadOf on the gateway would otherwise rewrite this push back into mqs://.
    sys.exit(subprocess.call(["git", *direct(url), "push", *atomic, *leases, url, *refspecs], env=env))


def _zero(sha: str) -> bool:
    return not sha.strip("0")


if __name__ == "__main__":
    main()

"""Builds dist/mqs: the whole package in one file. Stored uncompressed, so `unzip mqs` or any editor shows the source.

`python mqs install` sets a machine up; git runs it as git-remote-mqs; `mqs serve <peer>` runs the gateway.
"""
import io
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

from mqs.install import DEPS

ROOT = Path(__file__).parent
# Git for Windows runs extensionless helpers through sh; python skips this prefix when opening the zip.
# Resolve one symlink level (unix installs link into ~/.local/bin) so the .venv next to the real file wins.
PREAMBLE = b"""#!/bin/sh
self="$0"
if [ -L "$self" ]; then
  link="$(readlink "$self")"
  case "$link" in /*) self="$link" ;; *) self="$(dirname "$self")/$link" ;; esac
fi
dir="$(dirname "$self")"
[ -x "$dir/.venv/Scripts/python.exe" ] && exec "$dir/.venv/Scripts/python.exe" "$0" "$@"
[ -x "$dir/.venv/bin/python" ] && exec "$dir/.venv/bin/python" "$0" "$@"
command -v python >/dev/null && exec python "$0" "$@"
exec py -3 "$0" "$@"
"""


def build(out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(ROOT / "mqs", Path(tmp) / "mqs", ignore=shutil.ignore_patterns("__pycache__"))
        archive = io.BytesIO()
        zipapp.create_archive(tmp, archive, main="mqs.cli:main")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(PREAMBLE + archive.getvalue())
    out.chmod(0o755)  # git execs the helper straight from PATH
    return out


def write_launcher(bundle: Path) -> Path:
    """mqs.sh next to the bundle: runs it through uv, so no .venv is needed (the deps come from install.DEPS)."""
    out = bundle.with_name("mqs.sh")
    withs = " ".join(f"--with '{dep}'" for dep in DEPS)
    out.write_text(f'#!/bin/sh\nexec uv run --no-project --quiet {withs} python "$(dirname "$0")/{bundle.name}" "$@"\n')
    out.chmod(0o755)
    return out


if __name__ == "__main__":
    dist = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "mqs")
    print(dist, write_launcher(dist))

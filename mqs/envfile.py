"""The folder next to the bundle (.env, mirrors) and a .env reader. Stdlib only - install uses it before the deps exist."""
import sys
from pathlib import Path


def app_dir() -> Path:
    return Path(sys.argv[0]).resolve().parent  # resolve: unix installs reach the bundle via a symlink


def read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                values.setdefault(name.strip(), value.strip().strip("'\""))
    return values

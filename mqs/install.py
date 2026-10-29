"""mqs install / uninstall: deps, key, git config and PATH. Stdlib only - it runs before deps exist.

Every side effect is printed as it happens (what, and where), so the whole run is auditable.
"""
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

COMMAND = "mqs"
GIT_HELPER = "git-remote-mqs"  # git looks up remote helpers by this exact name
DEPS = ["awsiotsdk>=1.20", "cryptography>=42"]


def main():
    bundle = Path(sys.argv[0]).resolve()
    here = bundle.parent
    bundle.chmod(0o755)
    print(f"made {bundle} executable (755)")

    conf = read_config()
    py = ensure_venv(here)
    local = ask_choice("This machine is the", ["local workstation", "gateway that reaches the git server"]) == 0
    write_env(here)
    key = make_key(py, bundle) if local else ask("Paste the key printed by the local install")

    print("\ngit config (global):")
    set_cfg("mqs.endpoint", conf["endpoint"])
    for name in ("region", "topic"):
        if conf.get(name):
            set_cfg(f"mqs.{name}", conf[name])
    set_cfg("mqs.key", key)
    set_cfg("url.mqs://.insteadOf" if local else "mqs.upstream", conf["prefix"])

    print("commands on PATH:")
    install_commands(here, bundle)

    print("\ndone.")
    print("use git as usual - matching URLs tunnel automatically." if local
          else f"start the gateway while you work: {COMMAND} serve")


def ensure_venv(here: Path) -> Path:
    venv = here / ".venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    if not py.exists():
        print(f"creating virtualenv at {venv}")
        run(["uv", "venv", "--python", sys.executable, str(venv)] if uv else [sys.executable, "-m", "venv", str(venv)])
    # Always: a venv left behind by a failed install would otherwise never get its deps.
    print(f"installing {', '.join(DEPS)} into {venv}")
    run(["uv", "pip", "install", "--python", str(py), *DEPS] if uv else [str(py), "-m", "pip", "install", "--quiet", *DEPS])
    return py


def write_env(here: Path):
    env = here / ".env"
    if env.is_file() and ask_yes(f"{env.name} already exists, keep it?", True):
        print(f"kept existing {env}")
    else:
        key_id = ask("AWS_ACCESS_KEY_ID")
        secret = getpass.getpass("AWS_SECRET_ACCESS_KEY (hidden): ").strip()
        env.write_text(f"AWS_ACCESS_KEY_ID={key_id}\nAWS_SECRET_ACCESS_KEY={secret}\n", encoding="utf-8")
        print(f"wrote AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to {env}")
    if os.name != "nt":
        env.chmod(0o600)
        print(f"set {env} mode to 600")


def make_key(py: Path, bundle: Path) -> str:
    key = run([str(py), str(bundle), "keygen"], capture=True).strip()
    print(f"\n  KEY: {key}\n  Copy it, then run `{COMMAND} install` on the gateway and paste it.\n")
    return key


def install_commands(here: Path, bundle: Path):
    """Puts `mqs` and the git-remote-mqs alias on PATH; .venv/.env stay next to the real bundle."""
    if os.name == "nt":
        helper = here / GIT_HELPER
        if helper != bundle:
            shutil.copy(bundle, helper)  # extensionless, git runs it through sh
            print(f"  copied {bundle.name} -> {helper}")
        cmd = here / f"{COMMAND}.cmd"
        cmd.write_text(f'@"%~dp0.venv\\Scripts\\python.exe" "%~dp0{bundle.name}" %*\r\n')
        print(f"  wrote launcher {cmd}")
        add_windows_path(here)
    else:
        bindir = Path.home() / ".local" / "bin"
        if not bindir.exists():
            bindir.mkdir(parents=True)
            print(f"  created {bindir}")
        for name in (COMMAND, GIT_HELPER):
            link = bindir / name
            if link.resolve() == bundle:
                print(f"  {link} already points to the bundle")
                continue
            link.unlink(missing_ok=True)
            link.symlink_to(bundle)
            print(f"  symlinked {link} -> {bundle}")
        if str(bindir) not in os.environ.get("PATH", "").split(":"):
            print(f'  note: {bindir} is not on PATH - add to your profile: export PATH="$HOME/.local/bin:$PATH"')


def add_windows_path(here: Path):
    import ctypes
    import winreg
    root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE)
    parts = [p for p in _win_path(root).split(";") if p]
    if str(here) in parts:
        print(f"  {here} already on user PATH")
    else:
        winreg.SetValueEx(root, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts + [str(here)]))
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)
        print(f"  added {here} to user PATH (registry, HKCU\\Environment) - restart your terminal")
    winreg.CloseKey(root)


def uninstall():
    """Reverts install; prints each removal. Leaves the mqs file itself."""
    bundle = Path(sys.argv[0]).resolve()
    here = bundle.parent
    for section in git_sections(r"^mqs\.") | git_sections(r"^url\.mqs://"):
        run(["git", "config", "--global", "--remove-section", section], capture=True, check=False)
        print(f"removed git config section [{section}]")
    _rm(here / ".env")
    if os.name == "nt":
        _rm(here / GIT_HELPER)
        _rm(here / f"{COMMAND}.cmd")
        remove_windows_path(here)
    else:
        for name in (COMMAND, GIT_HELPER):
            link = Path.home() / ".local" / "bin" / name
            if link.is_symlink():
                link.unlink()
                print(f"removed symlink {link}")
    _rmtree(Path.home() / ".mqs")
    _rmtree(here / ".venv")
    print(f"\ndone. the {bundle.name} file is kept - delete it by hand if you want.")


def remove_windows_path(here: Path):
    import ctypes
    import winreg
    root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE)
    parts = [p for p in _win_path(root).split(";") if p]
    if str(here) in parts:
        winreg.SetValueEx(root, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(p for p in parts if p != str(here)))
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)
        print(f"removed {here} from user PATH")
    winreg.CloseKey(root)


def _win_path(root) -> str:
    import winreg
    try:
        return winreg.QueryValueEx(root, "Path")[0]
    except FileNotFoundError:
        return ""


def read_config() -> dict:
    """Shared, non-secret settings handed out as one JSON: endpoint, prefix, optional region and topic."""
    raw = ask("Paste the config JSON from your admin (or a path to a .json file)")
    source = Path(raw)
    try:
        conf = json.loads(source.read_text(encoding="utf-8") if source.is_file() else raw)
    except (json.JSONDecodeError, OSError, ValueError):
        sys.exit("mqs: config is not valid JSON")
    if not (conf.get("endpoint") and conf.get("prefix")):
        sys.exit('mqs: config needs "endpoint" and "prefix"')
    return conf


def git_sections(pattern: str) -> set[str]:
    out = run(["git", "config", "--global", "--name-only", "--get-regexp", pattern], capture=True, check=False)
    return {key.rsplit(".", 1)[0] for key in out.split()}


def set_cfg(key: str, value: str):
    run(["git", "config", "--global", key, value])
    print(f"  git config --global {key} {value}")


def _rm(path: Path):
    if path.exists():
        path.unlink()
        print(f"removed {path}")


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        print(f"removed {path}")


def ask(prompt: str, default: str = "") -> str:
    while True:
        value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        if value or default:
            return value or default


def ask_choice(prompt: str, options: list[str]) -> int:
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    while True:
        value = input(f"{prompt} [1-{len(options)}]: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value) - 1


def ask_yes(prompt: str, default: bool) -> bool:
    value = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not value else value.startswith("y")


def run(cmd: list[str], capture: bool = False, check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=capture, encoding="utf-8")
    if check and result.returncode:
        sys.exit(f"failed: {' '.join(cmd)}")
    return result.stdout or ""

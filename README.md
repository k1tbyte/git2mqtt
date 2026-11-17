# git2mqtt

Tunnels `git fetch / pull / push` over AWS IoT MQTT. A gateway machine runs the relay and talks to the git server; locally it stays plain git and your `origin` does not change.

Both machines need Python 3.10+ and git. The gateway must already be able to `git clone` from the server, and have `awsiotsdk` and `cryptography` in its python (`pip install awsiotsdk cryptography`). Your machine gets its own `.venv` from install.

## Settings

One `.env` next to the `mqs` file, the same on both machines:

```
MQS_ENDPOINT=a1b2c3-ats.iot.eu-west-1.amazonaws.com
MQS_UPSTREAM=https://git.example.com/
MQS_KEY=<any string, the same on both machines>
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

`MQS_UPSTREAM` is the start of your repo URLs, with the trailing `/`. Optional: `MQS_TOPIC` (default `mqs`), `MQS_REGION` (default: read from the endpoint), `MQS_PROFILE` (AWS profile instead of the keys).

`python mqs install` asks only for what `.env` lacks, so a ready `.env` means no questions. `python mqs keygen` prints a random key.

## 1. Your machine

1. Put `mqs` (and `.env`, if you have one) into a folder it will stay in, e.g. `C:\tools\mqs\`.
2. Open a terminal in that folder and run `python mqs install`. Choose `1`, answer what it asks.
3. Restart the terminal.

## 2. Gateway

1. Put `mqs` and the same `.env` into a permanent folder there.
2. Run `python mqs install` and choose `2`.

The gateway touches nothing outside its folder: no venv, no git config, no PATH. Repo mirrors live in `mirrors/` next to `mqs`.

## 3. Usage

1. On the gateway, from the folder with `mqs`: `python mqs serve`. It stops by itself after 2 idle minutes - just run it again when you need it.
2. On your machine: use git as usual. The first fetch of each repo is slow, then it's fast.

## Screen mode (RDP, PoC)

No network between the machines, only an RDP window: `MQS_ENDPOINT=screen://` and the same `MQS_KEY` and `MQS_UPSTREAM` in both `.env` files, plus `MQS_WINDOW=<part of the RDP window title>` on your machine. No AWS keys.

- Gateway (inside the RDP session): python with tkinter, git, `cryptography`. `python mqs serve` covers the screen with a colored grid, Esc stops it.
- Your machine: Windows, `mss` and `numpy` (install adds them). The helper switches the RDP window to fullscreen itself (no title bar, no scrollbars), so just use git as usual. Keep hands off keyboard and mouse while git runs: the helper focuses the window, types into it and minimizes it when done. If the gateway loses focus inside the session (its screen goes black), the helper clicks the middle of the window to bring it back. One git command at a time.
- Speed: fetch up to ~200 KB/s, push ~680 B/s.
- Check on one machine: `MQS_SCREEN_LOOPBACK=1 uv run pytest tests/test_e2e.py -k screen` (covers the screen and types for ~20 s).

## Remove it

Run `python mqs uninstall` on the machine. It undoes everything install did - git config, PATH entry, `.venv`/`.env`, mirrors - and keeps the `mqs` file itself.

## If something fails

- `no response for 60s`: start `serve` on the gateway (step 3).
- `clocks differ by more than 5 min`: fix the system time.
- `mqs: command not found` on macOS/Linux: add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile.
- `python` not found on Windows: use `py` instead.
- Repos cloned over ssh: `git config --global --add url."mqs://".insteadOf "git@git.example.com:"`
- Back to direct access: `git config --global --remove-section url.mqs://`

## Build

```
uv run bundle.py    # -> dist/mqs and dist/mqs.sh
```

`dist/mqs.sh <command>` runs `mqs` through uv with the deps fetched on the fly, no `.venv` needed.

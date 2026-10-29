# git2mqtt

Tunnels `git fetch / pull / push` over AWS IoT MQTT. A gateway machine runs the relay and talks to the git server; locally it stays plain git and your `origin` does not change.

Both machines need Python 3.10+ and git. The gateway must already be able to `git clone` from the server.

From whoever set up the AWS side you need two things: a config JSON (see below) and the AWS key id / secret.

## 1. Your machine

1. Put the `mqs` file into a folder it will stay in, e.g. `C:\tools\mqs\`.
2. Open a terminal in that folder and run `python mqs install`.
3. Answer:
   - Config JSON: paste it, or give a path to the `.json` file
   - This machine: `1`
   - AWS key id and secret
4. Copy the `KEY: ...` it prints. You need it in step 2.
5. Restart the terminal.

## 2. Gateway

1. Put `mqs` into a permanent folder there too.
2. Run `python mqs install` and answer:
   - Config JSON: the same one
   - This machine: `2`
   - AWS key id and secret: same as on your machine
   - Key: paste the key from step 1
3. Restart the terminal.

## 3. Usage

1. On the gateway: `mqs serve`. It stops by itself after 2 idle minutes - just run it again when you need it.
2. On your machine: use git as usual. The first fetch of each repo is slow, then it's fast.

## Config JSON

```json
{
  "endpoint": "a1b2c3-ats.iot.eu-west-1.amazonaws.com",
  "region": "eu-west-1",
  "topic": "mqs",
  "prefix": "https://git.example.com/"
}
```

`prefix` is the start of your repo URLs, with the trailing `/`. `region` and `topic` are optional - region is otherwise read from the endpoint, topic defaults to `mqs`. Everyone uses the same JSON.

## Remove it

Run `python mqs uninstall` on the machine. It undoes everything install did - git config, PATH entry, `.venv`/`.env`, mirrors - and keeps the `mqs` file itself.

## If something fails

- `no response for 60s`: start `mqs serve` on the gateway.
- `clocks differ by more than 5 min`: fix the system time.
- `mqs: command not found` on macOS/Linux: add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile.
- `python` not found on Windows: use `py` instead.
- Repos cloned over ssh: `git config --global --add url."mqs://".insteadOf "git@git.example.com:"`
- Back to direct access: `git config --global --remove-section url.mqs://`

## Build

```
uv run bundle.py    # -> dist/mqs
```

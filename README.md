# git2mqtt

Moves git commits between machines over AWS IoT MQTT. One file: `mqs.py`.

## Setup

```sh
pip install awsiotsdk        # or skip it and run `uv run mqs.py ...`
python -c "import secrets; print(secrets.token_hex(16))"   # MQS_KEY, same on both machines
```

`.env` next to `mqs.py`:

```
MQS_ENDPOINT=a1b2c3-ats.iot.eu-west-1.amazonaws.com
MQS_TOPIC=<topic prefix allowed by your IoT policy>
MQS_KEY=<same on both machines>
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

## Local -> remote

```sh
# local: repo with the commits, on the branch to send
python mqs.py --serve

# remote: repo to update
python mqs.py
```

The sender shows the bundle size and asks `y/n`. Start order does not matter.

## Remote -> local

```sh
# remote
python mqs.py --serve

# local
python mqs.py
```

## Repo path

Default is the current directory.

```sh
python mqs.py --serve ~/code/app
python mqs.py "C:\code\app"
```

## New empty repo

```sh
git init -b main
python mqs.py        # gets the whole branch
```

## Options

```sh
python mqs.py --idle 10    # wait 10 min for the other side (default 2)
```

## Notes

- Sends the sender's current branch after the newest commit both sides have; the whole branch if none are shared.
- The receiver fast-forwards the same-named branch. After a rebase it asks `Overwrite? [y/N]`; the old tip stays in `git reflog`.
- Keep merge commits and empty commits when rebasing: `git rebase --rebase-merges --empty=keep`

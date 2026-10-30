# /// script
# requires-python = ">=3.10"
# dependencies = ["awsiotsdk>=1.20"]
# ///
"""Ships the commits a repo is missing over AWS IoT MQTT, as a git bundle.

  python mqs.py --serve [REPO]   machine with the commits: offers the current branch of REPO
  python mqs.py [REPO]           repo that needs them: says where it is, applies what arrives

REPO defaults to the current directory.
"""
import argparse
import hashlib
import os
import queue
import re
import secrets
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

CHUNK = 64 * 1024  # AWS IoT messages are capped at 128 KiB
RATE = 400 * 1024  # bytes/s, below the AWS IoT 512 KiB/s per-connection cap
MIN_GAP = 0.02  # s, below the AWS IoT 100 publishes/s per-connection cap
RETRY = 5  # s between repeated requests: the sender may start after the receiver
REPLY_TIMEOUT = 60
RESULT_TIMEOUT = 300  # the receiver may be waiting on a y/n
HISTORY = 50  # commits the receiver lists, so the sender can find a shared one after a rebase
HASH = re.compile(r"[0-9a-f]{40,64}")
CONNECT_TIMEOUT = 30
QOS = mqtt.QoS.AT_LEAST_ONCE
HEADER = struct.Struct(">BI")  # kind, seq
HELLO, OFFER, DATA, OK, ERROR = range(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", nargs="?", default=".", help="path to the git repo (default: current directory)")
    parser.add_argument("--serve", action="store_true", help="send the repo's current branch instead of receiving")
    parser.add_argument("--idle", type=float, default=2, metavar="MIN",
                        help="give up after MIN minutes without a peer (default 2)")
    args = parser.parse_args()
    load_dotenv()  # before chdir: argv[0] may be a relative path
    try:
        os.chdir(args.repo)
        git("rev-parse", "--git-dir")
    except (OSError, RuntimeError) as e:
        sys.exit(f"mqs: {args.repo} is not a git repo - {e}")
    (serve if args.serve else pull)(args.idle * 60)


def serve(idle: float):
    branch = git("branch", "--show-current") or sys.exit("mqs: detached HEAD, check out a branch first")
    link = Link(serving=True)
    try:
        link.listen()
        print(f"waiting for a receiver, will offer {branch}", flush=True)
        while True:
            try:
                sid, kind, _, payload = link.recv(idle)
            except TimeoutError:
                sys.exit("mqs: no receiver showed up, closing")
            if kind == HELLO:
                break
        try:
            bundle = make_bundle(branch, payload.decode().split())
            print(f"{branch}: {_human(len(bundle))}, about {len(bundle) / RATE:.0f} s to send")
            if not confirm("Send?"):
                raise RuntimeError("sender declined")
            chunks = [bundle[i:i + CHUNK] for i in range(0, len(bundle), CHUNK)]
            link.send(sid, OFFER, str(len(chunks)).encode())
            for seq, chunk in enumerate(chunks):
                link.send(sid, DATA, chunk, seq)
                print(f"\rsent {seq + 1}/{len(chunks)}", end="", flush=True)
            kind, message = outcome(link, sid)
            print()
        except RuntimeError as e:
            kind, message = ERROR, str(e)
            link.send(sid, ERROR, message.encode())
        print(message)
        sys.exit(kind != OK)
    finally:
        link.close()


def make_bundle(branch: str, history: list[str]) -> bytes:
    ref = f"refs/heads/{branch}"  # full ref: a same-named tag would make the short one ambiguous
    if not all(HASH.fullmatch(sha) for sha in history):  # network input must not reach git as an option
        raise RuntimeError("receiver sent a malformed commit hash")
    base = next((sha for sha in history if has_commit(sha)), None)  # newest shared commit, also across a rebase
    if history and not base:
        print("none of the receiver's commits are known here, sending the whole branch")
    return git("bundle", "create", "--quiet", "-", f"{base}..{ref}" if base else ref, raw=True)


def has_commit(sha: str) -> bool:
    try:
        git("cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError:
        return False
    return True


def outcome(link: "Link", sid: str) -> tuple[int, str]:
    while True:
        try:
            frame_sid, kind, _, payload = link.recv(RESULT_TIMEOUT)
        except TimeoutError:
            return ERROR, f"no result for {RESULT_TIMEOUT} s, the receiver went silent"
        if frame_sid == sid and kind in (OK, ERROR):  # repeated requests may still be queued
            return kind, payload.decode()


def pull(idle: float):
    try:
        history = git("rev-list", f"--max-count={HISTORY}", "HEAD")
    except RuntimeError:
        history = ""  # no commits yet: the sender ships the whole branch
    sid = secrets.token_hex(8)
    link = Link(serving=False)
    try:
        link.listen(sid)
        print(f"at {history[:10] or 'no commits'}, waiting for a sender to offer more", flush=True)
        kind, payload = ask(link, sid, history, idle)
        if kind == ERROR:
            sys.exit(f"mqs: {payload.decode()}")
        try:
            message, kind = apply(receive(link, int(payload))), OK
        except RuntimeError as e:
            message, kind = str(e), ERROR
        link.send(sid, kind, message.encode())
        print(message)
        sys.exit(kind != OK)
    finally:
        link.close()


def ask(link: "Link", sid: str, history: str, idle: float) -> tuple[int, bytes]:
    deadline = time.monotonic() + idle
    while time.monotonic() < deadline:
        link.send(sid, HELLO, history.encode())
        try:
            _, kind, _, payload = link.recv(RETRY)
        except TimeoutError:
            continue
        if kind in (OFFER, ERROR):
            return kind, payload
    sys.exit("mqs: no sender showed up, closing")


def receive(link: "Link", count: int) -> bytes:
    chunks: dict[int, bytes] = {}
    while len(chunks) < count:
        try:
            _, kind, seq, payload = link.recv(REPLY_TIMEOUT)
        except TimeoutError:
            raise RuntimeError(f"transfer stalled for {REPLY_TIMEOUT} s") from None
        if kind == DATA and seq < count:
            chunks[seq] = payload  # a duplicate delivery just overwrites itself
    return b"".join(chunks[i] for i in range(count))


def apply(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "in.bundle"
        path.write_bytes(data)
        new, _, ref = git("bundle", "unbundle", str(path)).partition(" ")  # objects only; fails naming a missing base commit
    branch = ref.removeprefix("refs/heads/")
    try:
        old = git("rev-parse", "--verify", "--quiet", ref)
    except RuntimeError:
        old = ""
    dropped = int(git("rev-list", "--count", f"{new}..{old}")) if old else 0  # local commits a non-fast-forward would lose
    checked_out = git("branch", "--show-current") == branch and git("rev-parse", "--is-inside-work-tree") == "true"
    if dropped:
        if checked_out and git("diff", "--cached", "--name-only"):
            raise RuntimeError("staged changes would be lost, commit or stash them first")
        if not confirm(f"{branch}: {dropped} local commit(s) are not in the incoming history (tip {old[:10]}). Overwrite?"):
            raise RuntimeError("receiver declined")
    if not checked_out:
        git("update-ref", ref, new)
    elif dropped:
        git("reset", "--keep", new)  # aborts instead of clobbering local edits
    else:
        git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", new)  # neither may touch local files
    return f"{branch} -> {new[:10]}"


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def git(*args: str, raw: bool = False):
    proc = subprocess.run(["git", *args], capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return proc.stdout if raw else proc.stdout.decode().removesuffix("\n")  # not strip(): ref names may end in Unicode spaces


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def load_dotenv():
    """Settings may sit in a .env next to this file; real env vars win."""
    path = Path(sys.argv[0]).resolve().parent / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def need(name: str) -> str:
    return os.environ.get(name) or sys.exit(f"mqs: set {name} in .env next to this file")


def _resumed(connection, return_code, session_present, **_):
    # Clean sessions lose subscriptions on reconnect; a waiting side must get them back.
    if not session_present:
        connection.resubscribe_existing_topics()


class Link:
    """Framed MQTT channel: AWS IoT over SigV4 websockets, or plain `tcp://host:port` for local tests.

    Frames are plain: the key only names the private topic, it does not protect the payload.
    """

    def __init__(self, serving: bool):
        endpoint = need("MQS_ENDPOINT")
        channel = hashlib.blake2b(need("MQS_KEY").encode(), digest_size=8).hexdigest()
        self._base = f"{need('MQS_TOPIC')}/{channel}"
        self._out, self._in = ("down", "up") if serving else ("up", "down")
        self._next_at = 0.0
        self._inbox: queue.Queue = queue.Queue()
        client_id = f"mqs-{uuid.uuid4().hex}"
        if endpoint.startswith("tcp://"):
            host, port = endpoint[6:].rsplit(":", 1)
            self._conn = mqtt.Connection(
                client=mqtt.Client(None, None), host_name=host, port=int(port), client_id=client_id,
                clean_session=True, keep_alive_secs=30, on_connection_resumed=_resumed,
            )
        else:
            self._conn = mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=endpoint, region=os.environ.get("MQS_REGION") or _region(endpoint),
                credentials_provider=auth.AwsCredentialsProvider.new_default_chain(), client_id=client_id,
                clean_session=True, keep_alive_secs=30, on_connection_resumed=_resumed,
            )
        self._conn.connect().result(CONNECT_TIMEOUT)

    def listen(self, sid: str = "+"):
        future, _ = self._conn.subscribe(f"{self._base}/{sid}/{self._in}", QOS, self._on_message)
        future.result(CONNECT_TIMEOUT)

    def send(self, sid: str, kind: int, payload: bytes = b"", seq: int = 0):
        time.sleep(max(0.0, self._next_at - time.monotonic()))  # pacing, not a bucket: idle time must not become a burst
        frame = HEADER.pack(kind, seq) + payload
        self._next_at = time.monotonic() + max(MIN_GAP, len(frame) / RATE)
        future, _ = self._conn.publish(f"{self._base}/{sid}/{self._out}", frame, QOS)
        future.result(CONNECT_TIMEOUT)

    def recv(self, timeout: float) -> tuple[str, int, int, bytes]:
        """Next (sid, kind, seq, payload); TimeoutError when nothing arrives."""
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self):
        self._conn.disconnect().result(CONNECT_TIMEOUT)

    def _on_message(self, topic, payload, **_):
        if len(payload) >= HEADER.size:
            kind, seq = HEADER.unpack_from(payload)
            self._inbox.put((topic.split("/")[-2], kind, seq, payload[HEADER.size:]))


def _region(endpoint: str) -> str:
    """AWS IoT endpoints look like <id>-ats.iot.<region>.amazonaws.com."""
    return endpoint.split(".iot.")[1].split(".")[0] if ".iot." in endpoint else ""


if __name__ == "__main__":
    main()

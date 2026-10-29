"""git-remote-mqs: git spawns it for mqs://<path> urls and talks the pack protocol through it."""
import json
import os
import secrets
import sys
import threading
import time

from . import config
from .broker import Broker
from .stream import CHUNK, DATA, END, ERROR, HELLO, READY, STATUS, Stream

IDLE = 60


def main():
    path = sys.argv[2].removeprefix("mqs://")
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for line in iter(stdin.readline, b""):
        cmd = line.strip()
        if cmd == b"capabilities":
            stdout.write(b"connect\n\n")
            stdout.flush()
        elif cmd.startswith(b"connect "):
            sys.exit(tunnel(path, cmd[8:].decode()))
        else:
            return


def tunnel(path: str, service: str) -> int:
    cfg = config.load()
    broker = Broker(cfg)
    try:
        stream = Stream(broker, cfg, secrets.token_hex(8), b"c")
        broker.subscribe(stream.recv_topic, lambda _, frame: stream.feed(frame))
        stream.send(HELLO, json.dumps({"service": service, "path": path, "ts": time.time()}).encode())
        return relay(stream)
    finally:
        broker.close()


def relay(stream: Stream) -> int:
    out, err = sys.stdout.buffer, sys.stderr.buffer
    while True:
        try:
            kind, data = stream.recv(IDLE)
        except TimeoutError:
            return fail(f"no response for {IDLE}s, is `mqs serve` running on the gateway?")
        if kind == READY:
            # Git starts talking only after this blank line, so serve is already listening.
            out.write(b"\n")
            out.flush()
            threading.Thread(target=pump_stdin, args=(stream,), daemon=True).start()
            stream.start_heartbeat()  # only now: serve drops frames that arrive before its session exists
        elif kind == DATA:
            out.write(data)
            out.flush()
        elif kind == STATUS:
            err.write(data)
            err.flush()
        elif kind == END:
            return int(data)
        elif kind == ERROR:
            return fail(data.decode())


def pump_stdin(stream: Stream):
    # Raw os.read: a daemon thread blocked in the buffered reader holds its lock and crashes interpreter exit.
    try:
        while chunk := os.read(sys.stdin.fileno(), CHUNK):
            stream.send(DATA, chunk)
        stream.send(END)
    except Exception as e:
        stream.abort(f"upload failed: {e}")  # heartbeats would otherwise keep the dead session alive


def fail(message: str) -> int:
    print(f"fatal: mqs: {message}", file=sys.stderr)
    return 128

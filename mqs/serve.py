"""mqs serve: answers helper sessions with git processes running on local mirrors."""
import json
import subprocess
import threading
import time

from . import config, mirror
from .broker import Broker
from .stream import CHUNK, DATA, END, ERROR, HELLO, READY, STATUS, Stream

SERVICES = {"git-upload-pack", "git-receive-pack"}
MAX_SKEW = 300
IDLE = 120


def serve(idle_min: float = 2):
    cfg = config.load(server=True)
    broker = Broker(cfg)
    sessions: dict[str, Stream] = {}
    seen: set[str] = set()
    lock = threading.Lock()  # one user per serve: sessions run one at a time
    state = threading.Lock()  # short-held: admission vs idle shutdown must not interleave
    idle = idle_min * 60
    last = time.monotonic()
    closing = False

    def run(sid: str, stream: Stream, hello: dict):
        heartbeat = stream.start_heartbeat()  # keeps the helper waiting through lock waits and mirror clones
        try:
            print(f"{time.strftime('%H:%M:%S')} {hello['service']} {hello['path']}", flush=True)
            if hello["service"] not in SERVICES:
                raise ValueError(f"unsupported service {hello['service']}")
            if abs(time.time() - hello["ts"]) > MAX_SKEW:
                raise ValueError("clocks differ by more than 5 min, fix system time")
            with lock:
                down, up = pipe(stream, ["git", hello["service"][4:], mirror.sync(cfg.upstream, hello["path"])])
            print(f"  down {_human(down)}  up {_human(up)}", flush=True)
        except Exception as e:
            print(f"  error: {e}", flush=True)
            stream.send(ERROR, str(e).encode())
        finally:
            heartbeat.set()
            with state:
                sessions.pop(sid, None)

    def on_frame(topic: str, frame: bytes):
        nonlocal last
        with state:
            last = time.monotonic()
        sid = topic.split("/")[-2]
        if sid in sessions:
            return sessions[sid].feed(frame)
        if sid in seen:
            return
        stream = Stream(broker, cfg, sid, b"s")
        stream.feed(frame)
        try:
            kind, body = stream.recv(0)
        except TimeoutError:
            return
        if kind == HELLO:
            with state:
                if closing:
                    return
                seen.add(sid)  # replay guard together with the ts check
                sessions[sid] = stream
            threading.Thread(target=run, args=(sid, stream, json.loads(body)), daemon=True).start()

    broker.subscribe(f"{cfg.base}/+/c", on_frame)
    stop = f"idle {idle_min:g} min" if idle else "Ctrl+C"
    print(f"serving {cfg.upstream}, stops on {stop}", flush=True)
    try:
        while not closing:
            time.sleep(min(idle or 3600, 30))  # bounded: Event.wait() ignores Ctrl+C on Windows
            with state:
                closing = bool(idle) and not sessions and time.monotonic() - last > idle
        print(f"idle for {idle_min:g} min, closing connection", flush=True)
    except KeyboardInterrupt:
        pass
    broker.close()


def pipe(stream: Stream, cmd: list[str]) -> tuple[int, int]:
    """Returns (bytes sent to client, bytes received from client) - the git payload over MQTT."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    up = [0]
    try:
        stream.send(READY)
        threading.Thread(target=_feed_stdin, args=(stream, proc, up), daemon=True).start()
        stderr = threading.Thread(target=_forward, args=(proc.stderr, stream, STATUS))
        stderr.start()
        down = _forward(proc.stdout, stream, DATA)
        stderr.join()
        stream.send(END, str(proc.wait()).encode())
        return down, up[0]
    finally:
        proc.kill()  # no-op after a normal exit; frees a git process stuck on a failed stream


def _forward(src, stream: Stream, kind: int) -> int:
    total = 0
    while chunk := src.read1(CHUNK):
        stream.send(kind, chunk)
        total += len(chunk)
    return total


def _feed_stdin(stream: Stream, proc: subprocess.Popen, up: list[int]):
    try:
        while True:
            kind, data = stream.recv(IDLE)
            if kind == STATUS:
                continue  # helper heartbeat
            if kind != DATA:
                break
            up[0] += len(data)
            proc.stdin.write(data)
            proc.stdin.flush()
        proc.stdin.close()
    except TimeoutError:
        proc.kill()
    except OSError:
        pass  # process already exited


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

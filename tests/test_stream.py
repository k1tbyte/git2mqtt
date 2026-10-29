import random

import pytest

from mqs.config import Config
from mqs.stream import DATA, ERROR, HEADER, NONCE, Stream

KEY = bytes(range(16))
OTHER_KEY = bytes(range(16, 32))
SID = "sid"
TIMEOUT = 0.05


class FakeBroker:
    def __init__(self):
        self.published = []
        self._subscribers = {}

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        for callback in self._subscribers.get(topic, []):
            callback(topic, payload)

    def subscribe(self, topic, callback):
        self._subscribers.setdefault(topic, []).append(callback)

    @property
    def frames(self):
        return [frame for _, frame in self.published]


def cfg(key=KEY):
    return Config(key=key, endpoint="", region="", topic="t", profile="", upstream="")


def drain(stream):
    out = []
    while True:
        try:
            out.append(stream.recv(TIMEOUT))
        except TimeoutError:
            return out


@pytest.fixture
def pair():
    broker = FakeBroker()
    return broker, Stream(broker, cfg(), SID, b"c"), Stream(broker, cfg(), SID, b"s")


def test_both_directions_through_broker(pair):
    broker, client, server = pair
    broker.subscribe(server.recv_topic, lambda _, frame: server.feed(frame))
    broker.subscribe(client.recv_topic, lambda _, frame: client.feed(frame))

    client.send(DATA, b"ping")
    server.send(DATA, b"pong")

    assert drain(server) == [(DATA, b"ping")]
    assert drain(client) == [(DATA, b"pong")]
    assert [topic for topic, _ in broker.published] == [server.recv_topic, client.recv_topic]


def test_shuffled_and_duplicated_frames_arrive_in_order_once(pair):
    broker, client, server = pair
    sent = [(i % 6, bytes([i]) * 3) for i in range(20)]
    for kind, payload in sent:
        client.send(kind, payload)

    frames = broker.frames * 2
    random.Random(1).shuffle(frames)
    for frame in frames:
        server.feed(frame)

    assert drain(server) == sent


@pytest.mark.parametrize(
    "offset", [0, HEADER.size - 1, HEADER.size, HEADER.size + NONCE, -1], ids=["seq", "kind", "nonce", "body", "tag"]
)
def test_tampered_frame_is_dropped_and_does_not_block_the_genuine_one(pair, offset):
    broker, client, server = pair
    for payload in (b"zero", b"one", b"two"):
        client.send(DATA, payload)
    first, second, third = broker.frames
    tampered = bytearray(second)
    tampered[offset] ^= 0x01

    for frame in (first, bytes(tampered), third):
        server.feed(frame)
    assert drain(server) == [(DATA, b"zero")]

    server.feed(second)
    assert drain(server) == [(DATA, b"one"), (DATA, b"two")]


@pytest.mark.parametrize("key, sid", [(OTHER_KEY, SID), (KEY, "other-sid")], ids=["other-key", "other-sid"])
def test_frame_from_another_key_or_session_is_dropped(pair, key, sid):
    broker, _, server = pair
    foreign_broker = FakeBroker()
    Stream(foreign_broker, cfg(key), sid, b"c").send(DATA, b"forged")
    Stream(broker, cfg(), SID, b"c").send(DATA, b"genuine")

    server.feed(foreign_broker.frames[0])
    assert drain(server) == []

    server.feed(broker.frames[0])
    assert drain(server) == [(DATA, b"genuine")]


@pytest.mark.parametrize("side", [b"c", b"s"])
def test_stream_ignores_its_own_frames(side):
    broker = FakeBroker()
    stream = Stream(broker, cfg(), SID, side)
    stream.send(DATA, b"echo")

    for frame in broker.frames:
        stream.feed(frame)

    assert drain(stream) == []


def test_truncated_frame_is_dropped(pair):
    _, _, server = pair
    server.feed(b"\x00" * (HEADER.size - 1))
    assert drain(server) == []


def test_abort_ends_recv_with_local_error(pair):
    _, client, _ = pair

    client.abort("upload failed")

    assert client.recv(TIMEOUT) == (ERROR, b"upload failed")

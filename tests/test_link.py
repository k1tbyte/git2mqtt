import random

import pytest

from mqs.screen.link import Link, MAX_PAYLOAD


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def flaky(rng, packet, loss):
    if packet is None or rng.random() < loss:
        return None
    if rng.random() < loss / 2:  # a damaged packet, as the screen or the keys deliver them
        i = rng.randrange(len(packet))
        return packet[:i] + bytes([packet[i] ^ 1 << rng.randrange(8)]) + packet[i + 1:]
    return packet


def pair(clock, a_rto=0.0, b_rto=3.0):
    got_a, got_b = [], []
    a = Link(mtu=40, window=16, rto=a_rto, on_message=got_a.append, epoch=7, clock=clock)  # epochs: tested apart
    b = Link(mtu=40, window=8, rto=b_rto, on_message=got_b.append, epoch=7, clock=clock)
    return a, b, got_a, got_b


def run(a, b, clock, rng, loss, until, steps=4000):
    """a plays the screen (a frame every tick, idle ones too), b the keys (only when it has something to say)."""
    for _ in range(steps):
        clock.t += 0.2
        if packet := flaky(rng, a.next_packet(force=True), loss):
            b.feed(packet)
        if packet := flaky(rng, b.next_packet(), loss):
            a.feed(packet)
        if until():
            return True
    return False


def test_messages_arrive_in_order_both_ways_despite_loss_and_damage():
    clock, rng = Clock(), random.Random(1)
    a, b, got_a, got_b = pair(clock)
    to_b = [bytes([i]) * rng.randrange(1, 400) for i in range(30)]
    to_a = [bytes([100 + i]) * rng.randrange(1, 200) for i in range(10)]
    for m in to_b:
        a.send(m)
    for m in to_a:
        b.send(m)

    assert run(a, b, clock, rng, loss=0.3, until=lambda: len(got_a) == len(to_a) and len(got_b) == len(to_b))

    assert got_b == to_b and got_a == to_a


def test_idle_end_acks_with_ack_only_packets_so_the_sender_stops_resending():
    clock, rng = Clock(), random.Random(2)
    a, b, _, got_b = pair(clock)
    a.send(b"x" * 100)

    assert run(a, b, clock, rng, loss=0.0, until=lambda: len(got_b) == 1)

    for _ in range(3):  # the acks are on their way back
        run(a, b, clock, rng, loss=0.0, until=lambda: False, steps=1)
    packets = {a.next_packet() for _ in range(5)}
    assert packets == {None}


def test_damaged_or_foreign_packets_are_dropped():
    a, b, _, got_b = pair(Clock())
    a.send(b"hello")
    packet = a.next_packet()

    b.feed(b"")
    b.feed(b"junk" * 10)
    b.feed(packet[:-1] + bytes([packet[-1] ^ 1]))
    assert got_b == []

    b.feed(packet)
    assert got_b == [b"hello"]


@pytest.mark.parametrize("old,new", [(5, 6), (2**32 - 2, 3)])  # the second pair wraps around
def test_new_helper_session_restarts_the_gateway_link(old, new):
    clock, rng = Clock(), random.Random(3)
    got_screen, got_keys = [], []
    screen = Link(mtu=40, window=16, rto=0, on_message=got_screen.append, clock=clock)
    first = Link(mtu=40, window=8, rto=3, on_message=got_keys.append, epoch=old, clock=clock)
    first.send(b"one" * 50)
    assert run(screen, first, clock, rng, loss=0, until=lambda: got_screen)  # contact: the gateway adopts the epoch
    screen.send(b"for-first")
    stale = first.next_packet(force=True)

    got_screen.clear()
    second = Link(mtu=40, window=8, rto=3, on_message=got_keys.append, epoch=new, clock=clock)
    second.send(b"two" * 50)
    assert run(screen, second, clock, rng, loss=0, until=lambda: got_screen)
    screen.send(b"for-second")
    screen.feed(stale)

    assert run(screen, second, clock, rng, loss=0, until=lambda: got_keys)
    assert got_screen == [b"two" * 50] and got_keys == [b"for-second"]


def test_packet_payload_never_exceeds_the_header_length_field():
    link = Link(mtu=10**6, window=4, rto=0, on_message=lambda m: None)
    link.send(bytes(200_000))

    packet = link.next_packet()

    assert packet is not None and len(packet) <= MAX_PAYLOAD + 20


def test_gateway_takes_any_epoch_after_a_long_silence():
    clock, rng = Clock(), random.Random(4)
    got = []
    screen = Link(mtu=40, window=16, rto=0, on_message=got.append, clock=clock)

    for epoch in (5, 5 + 2**31 + 10):  # the second is not "newer" by the half-range rule
        helper = Link(mtu=40, window=8, rto=3, on_message=lambda m: None, epoch=epoch, clock=clock)
        helper.send(b"hello")
        clock.t += 400
        assert run(screen, helper, clock, rng, loss=0, until=lambda: got)
        got.clear()

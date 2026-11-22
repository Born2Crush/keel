"""Keel decides whether a PS5 stream can be shown, and restarts one whose start is missing. No window."""

from framegen.chiaki_tap import SLOTS
from framegen.session_owner import SessionError, header_matches, join_plan, own_session

EARLY = SLOTS - 4


class H:
    def __init__(self, live=False, write_seq=0, session=1, codec=1):
        self.live, self.write_seq, self.session, self.codec = live, write_seq, session, codec


def test_a_quiet_tap_needs_a_start_and_an_early_stream_can_be_joined():
    assert join_plan(False, 0, 1, 1, b"") == "start"
    assert join_plan(True, 0, 1, 1, b"") == "join"
    assert join_plan(True, EARLY, 1, 1, b"") == "join"


def test_a_stream_that_already_started_is_restarted_unless_its_header_was_saved():
    assert join_plan(True, EARLY + 1, 7, 1, b"") == "restart"
    blob = header_blob(7, 1)
    assert header_matches(blob, 7, 1) and not header_matches(blob, 8, 1)
    assert join_plan(True, 100, 7, 1, blob) == "join"
    assert join_plan(True, 100, 8, 1, blob) == "restart"


def header_blob(session: int, codec: int) -> bytes:
    import struct
    return struct.pack("<II", session, codec) + b"\x00\x00\x01params"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class Pad:
    """chiaki-ng's window, scripted. `live_after` says which call makes a fresh stream appear."""

    def __init__(self, header: H, live_after: str | None = None):
        self.h, self.calls, self.live_after = header, [], live_after

    def ensure_running(self):
        self.calls.append("ensure")

    def connect(self):
        self.calls.append("connect")
        self._maybe("connect")

    def end_stream(self):
        self.calls.append("end")
        self._maybe("end")

    def decline_sleep(self):
        self.calls.append("decline")
        self._maybe("decline")

    def _maybe(self, name: str) -> None:
        if name == self.live_after:
            self.h.live, self.h.write_seq, self.h.session = True, 0, self.h.session + 1


def test_a_stream_whose_start_is_in_the_tap_is_left_alone():
    clock, pad = Clock(), Pad(H(True, 4, 3, 1))
    assert own_session(lambda: pad.h, pad, b"", sleep=clock.sleep, clock=clock) == "join"
    assert pad.calls == ["ensure"]


def test_a_dead_tap_gets_a_connect_and_then_joins_the_new_stream():
    clock, pad = Clock(), Pad(H(), live_after="connect")
    assert own_session(lambda: pad.h, pad, b"", sleep=clock.sleep, clock=clock) == "join"
    assert pad.calls == ["ensure", "connect"]
    assert pad.h.write_seq == 0 and clock.t >= 2.0  # autoconnect was given a moment first


def test_a_stream_that_started_earlier_is_ended_and_connected_again():
    clock, pad = Clock(), Pad(H(True, 400, 5, 1), live_after="connect")
    pad.h_end_clears = False

    real_end = pad.end_stream

    def end():
        real_end()
        pad.h.live = False  # the x worked; no sleep question

    pad.end_stream = end
    assert own_session(lambda: pad.h, pad, b"", sleep=clock.sleep, clock=clock) == "join"
    assert pad.calls[:3] == ["ensure", "end", "connect"]
    assert "decline" not in pad.calls
    assert pad.h.session == 6 and pad.h.write_seq == 0


def test_a_sleep_question_is_answered_no_before_connecting_again():
    clock, pad = Clock(), Pad(H(True, 400, 5, 1), live_after="decline")

    def decline():
        pad.calls.append("decline")
        pad.h.live = False

    pad.decline_sleep = decline

    def connect():
        pad.calls.append("connect")
        pad.h.live, pad.h.write_seq, pad.h.session = True, 1, 9

    pad.connect = connect
    assert own_session(lambda: pad.h, pad, b"", sleep=clock.sleep, clock=clock) == "join"
    assert pad.calls == ["ensure", "end", "decline", "connect"]


def test_giving_up_when_the_stream_never_comes():
    clock, pad = Clock(), Pad(H())
    try:
        own_session(lambda: pad.h, pad, b"", sleep=clock.sleep, clock=clock, timeout_s=3.0)
    except SessionError as e:
        assert "from its start" in str(e)
    else:
        raise AssertionError("expected SessionError")
    assert clock.t >= 3.0

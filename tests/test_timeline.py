"""Even timing for 40-60 fps games (framegen/cadence.py FrameTimeline), on simulated games: frames drawn at even
times, each shown from the first 60 Hz tick after it's done. CPU only."""
import math
import time

import numpy as np
import pytest
import torch

from framegen.cadence import FrameTimeline
from framegen.io import Tick
from framegen.settings import Settings
from test_game_rate import _pipeline


def _game_times(fps, ticks, phase=0.37, jitter=0.0, seed=0):
    """{arrival tick: the time (in ticks) the game drew the frame shown from it}."""
    rng = np.random.default_rng(seed)
    t, arr = phase, {}
    while t < ticks:
        arr[math.ceil(t)] = t  # a later frame done in the same tick replaces it
        t += 60 / fps * (1 + jitter * rng.normal())
    return arr


def _play(arr, ticks, hz):
    """Where in the game's time each output frame is (linear motion), and how many waited for a late frame."""
    tl = FrameTimeline()
    pos, waits = [], 0
    for n in range(ticks):
        tl.tick(n in arr, frame=arr.get(n), t_arrival=n)
        if tl.uneven:
            for phi in tl.slots(hz):
                p = tl.place(phi)
                waits += p.waiting
                pos.append(p.a.frame if p.b is None else p.a.frame + p.t * (p.b.frame - p.a.frame))
    return np.array(pos), waits, tl


@pytest.mark.parametrize("fps", [40, 45, 48, 50, 55])
@pytest.mark.parametrize("hz", [60, 90, 120])
def test_motion_is_even_at_any_rate_in_between(fps, hz):
    pos, waits, tl = _play(_game_times(fps, 900), 900, hz)
    steps = np.diff(pos[60:])
    assert tl.uneven and waits == 0
    assert np.abs(steps - 60 / hz).max() < 0.1  # shown as they come, a step is off by up to a whole tick
    assert 1.0 <= tl.delay <= 1.6


def test_30_and_60_fps_games_are_left_to_the_cadence_scheduler():
    for fps in (30, 60):
        _, _, tl = _play(_game_times(fps, 600, phase=0.0), 600, 60)
        assert not tl.uneven


def test_a_menu_at_60_in_a_30_fps_game_doesnt_switch_it_on():
    arr = {n: n for n in range(0, 300, 2)} | {n: n for n in range(300, 330)} | {n: n for n in range(330, 600, 2)}
    tl = FrameTimeline()
    on = 0
    for n in range(600):
        tl.tick(n in arr)
        on += tl.uneven
    assert on == 0


def test_a_jittery_game_and_a_changing_rate_stay_close():
    for fps, jitter in ((45, 0.05), (50, 0.1)):
        pos, waits, _ = _play(_game_times(fps, 900, jitter=jitter), 900, 90)
        assert np.std(np.diff(pos[60:]) - 2 / 3) < 0.1 and waits < 30
    tl, pos = FrameTimeline(), []
    t, arr = 0.3, {}
    while t < 900:
        arr[math.ceil(t)] = t
        t += 60 / (45 + 10 * t / 900)  # 45 to 55 fps
    pos, waits, _ = _play(arr, 900, 120)
    assert np.std(np.diff(pos[60:]) - 0.5) < 0.05 and waits < 20


def test_a_hitch_waits_then_carries_on():
    arr = _game_times(45, 600)
    arr = {n: t for n, t in arr.items() if not 300 <= n < 306}  # the game stops for 6 ticks
    pos, waits, tl = _play(arr, 600, 60)
    assert 1 <= waits <= 8 and tl.uneven
    assert np.all(np.diff(pos) >= -1e-9)  # never back in time
    assert np.abs(np.diff(pos[-120:]) - 1).max() < 0.1  # and even again after


def test_no_blend_across_a_cut():
    tl = FrameTimeline()
    arr = _game_times(45, 300)
    blended = []
    for n in range(300):
        cut = n == 200 and n in arr
        tl.tick(n in arr, is_cut=cut or (n == 201 and 200 not in arr), frame=n)
        if tl.uneven:
            for phi in tl.slots(60):
                p = tl.place(phi)
                if p.b is not None and p.b.cut:
                    blended.append(n)
    assert not blended


def test_slots_follow_the_output_rate():
    tl = FrameTimeline()
    counts = {60: [], 90: [], 120: []}
    for hz in counts:
        for _ in range(12):
            tl.tick(True)
            counts[hz].append(len(tl.slots(hz)))
    assert counts[60][1:] == [1] * 11 and sum(counts[90][1:]) in (16, 17) and counts[120][1:] == [2] * 11


# ---- in the pipeline: the right two frames in the interpolator, at the right fraction
class _BlendInterp:
    """Interpolates by blending, so an output frame's value says which game time it shows."""
    h = w = 8
    device = "cpu"
    private = False

    def __init__(self):
        self.in_u8 = self.out_u8 = torch.zeros(8, 8, 3)
        self.slots = [None, None]
        self.loads = 0

    def load(self, frame):
        self.in_u8 = frame
        self.loads += 1

    def prepare(self, slot):
        self.slots[slot] = float(self.in_u8[0, 0, 0])

    def interpolate(self, slot, t):
        a, b = self.slots[1 - slot], self.slots[slot]
        self.out_u8 = torch.full((8, 8, 3), a + t * (b - a))
        return self.out_u8


class _Sink:
    gpu = False

    def __init__(self):
        self.values, self.toasts = [], []

    def write(self, frame, text=None):
        self.values.append(float(frame[0, 0, 0]))

    def toast(self, text, seconds=2.0):
        self.toasts.append(text)


def _stream(arr, ticks):
    shown = 0.0
    for n in range(ticks):
        new = n in arr
        shown = arr[n] if new else shown
        yield Tick(torch.full((8, 8, 3), shown), time.perf_counter(), n, new_hint=new)


@pytest.mark.parametrize("hz", [60, 90])
def test_the_pipeline_shows_even_motion(hz):
    p = _pipeline()
    p.interp = _BlendInterp()
    p._out_pinned = torch.empty((8, 8, 3))  # (float, so the values aren't rounded)
    p._out_np = p._out_pinned.numpy()
    sink = _Sink()
    ticks = 260
    st = p.run(_stream(_game_times(45, ticks), ticks), sink, label="RIFE", settings=Settings({"hz": hz}))
    assert st.uneven_ticks > 100 and st.uneven_waits == 0
    v = np.array(sink.values[-int(st.uneven_ticks * hz / 60) + 30:])
    assert np.abs(np.diff(v) - 60 / hz).max() < 0.1, np.diff(v)
    # and the even timing off: shown as they come, unevenly
    sink2 = _Sink()
    p2 = _pipeline()
    p2.interp = _BlendInterp()
    p2._out_pinned = torch.empty((8, 8, 3))
    p2._out_np = p2._out_pinned.numpy()
    st2 = p2.run(_stream(_game_times(45, ticks), ticks), sink2, label="RIFE",
                 settings=Settings({"hz": 60, "even_out": False}))
    assert st2.uneven_ticks == 0 and np.abs(np.diff(sink2.values[-100:]) - 1).max() > 0.3

"""What the overlay and test mode's report say about the game's real frame rate: the game's rate and frames shown
per second, the advice when a game isn't at the rate frame generation (or test mode) needs, test mode leaving out
scores where the game itself repeated frames, and the verdict. CPU only: a stand-in interpolator, no GPU, no
window."""
import time

import numpy as np
import torch

from framegen import pipeline as pl
from framegen.io import Tick
from framegen.pipeline import GameRate, Pipeline, ShownRate, Stats, rate_advice
from framegen.settings import Settings
from framegen.sources import TestMode


def test_game_rate_counts_new_frames_over_two_seconds_of_ticks():
    r = GameRate()
    for i in range(119):
        r.add(i % 2 == 0)
    assert r.fps() is None  # not a whole window yet
    r.add(False)
    assert r.fps() == 30.0
    r.add(None)  # the source can't say: left out, not counted as a repeat
    assert r.fps() == 30.0
    for i in range(120):
        r.add(i % 4 != 3)  # three new frames in every four ticks
    assert r.fps() == 45.0
    r.clear()
    assert r.fps() is None


def test_shown_rate_is_frames_on_screen_in_the_last_second():
    r = ShownRate()
    for i in range(91):
        r.add(i / 90)
    assert r.fps() == 90.0
    for i in range(1, 61):
        r.add(1 + i / 60)  # down to 60 a second
    assert r.fps() == 60.0
    assert ShownRate().fps() is None


def test_rate_advice():
    assert rate_advice(None, False) == "" and rate_advice(None, True) == ""
    assert rate_advice(30.5, False) == "" and rate_advice(34.0, False) == ""  # a 30 fps game with the odd hitch
    assert "about 46 fps, not 30" in rate_advice(46.0, False) and "30 fps mode" in rate_advice(46.0, False)
    assert "Game at 60 fps: no gaps to fill" in rate_advice(59.6, False)
    assert rate_advice(59.0, True) == ""  # test mode with a 60 fps game: as it should be
    assert "at 30 fps already: turn Simulate off" in rate_advice(30.0, True)
    assert "about 44, so quality isn't measured" in rate_advice(44.0, True)
    for g in (30, 44, 46, 60):  # fits the overlay's line (about 1500 px at its font size)
        assert len(rate_advice(g, False)) <= 100 and len(rate_advice(g, True)) <= 100


def _tick(i, new):
    return Tick(torch.full((8, 8, 3), i, dtype=torch.uint8), 0.0, i, new_hint=new)


def test_test_mode_notes_the_games_own_repeats_and_scores_only_fair_frames():
    tm = TestMode(True)
    # a 60 fps game: every frame new, so every left-out frame can be scored
    ticks = [tm(_tick(i, True)) for i in range(8)]
    assert [t.extra["game_new"] for t in ticks] == [True] * 8
    assert tm.gt(5) is not None
    # a 30 fps game (A A B B): the frame left out is just the kept one again
    tm = TestMode(True)
    ticks = [tm(_tick(i, i % 2 == 0)) for i in range(8)]
    assert [t.extra["game_new"] for t in ticks] == [True, False] * 4
    assert all(tm.gt(i) is None for i in (1, 3, 5))
    # the other phase (B new on the odd ticks): the left-out frame is new, but the kept ones around it are repeats
    tm = TestMode(True)
    [tm(_tick(i, i % 2 == 1)) for i in range(8)]
    assert all(tm.gt(i) is None for i in (1, 3, 5))
    # no word from the source (it compares pictures): scored as before
    tm = TestMode(True)
    [tm(_tick(i, None)) for i in range(8)]
    assert tm.gt(5) is not None


def test_results_say_when_test_mode_had_no_60_fps_game_and_judge_by_extra_frames():
    st = Stats(proc_interp_ms=[12.0] * 100, busy_ms=[9.0] * 1000, real_delay_ms=[30.0] * 100, hz_used={90},
               quarter_shown=96, quarter_skipped=4, behind=177, simulated=1000, test_game_new=500,
               test_game_repeat=500)
    text = "\n".join(st.results())
    assert "about 30 fps, not a steady 60" in text and "doesn't need it" in text
    assert "Quality: not measured, as the game wasn't at a steady 60 fps" in text
    # 18% of ticks came in while busy, but 96% of the extra frames were shown: that's keeping up at 90
    assert st.results()[-1] == "Verdict: this PC keeps up at 90 fps"
    st.quarter_shown, st.quarter_skipped = 85, 15
    assert "mostly keeps up at 90 fps" in st.results()[-1]
    st.quarter_shown, st.quarter_skipped = 70, 30
    assert "falling behind at 90 fps" in st.results()[-1]
    # a steady 60 fps game in test mode: no note, scores as usual
    st = Stats(proc_interp_ms=[8.0] * 10, busy_ms=[9.0] * 10, psnr_interp=[34.0] * 10, psnr_repeat=[26.0] * 10, psnr_blend=[30.0] * 10,
               hz_used={60}, test_game_new=1990, test_game_repeat=10)
    text = "\n".join(st.results())
    assert "Game:" not in text and "34.0 dB" in text and "(10 frames measured, worst 34.0 dB)" in text
    assert "10 of 2000 frames were repeats" in st.summary()


# ---- the overlay and the message, in a real pipeline run with a stand-in interpolator
class _Interp:
    h = w = 8
    device = "cpu"
    private = False

    def __init__(self):
        self.in_u8 = self.out_u8 = torch.zeros(8, 8, 3, dtype=torch.uint8)

    def load(self, frame):
        self.in_u8 = frame

    def prepare(self, slot):
        pass

    def interpolate(self, slot, t):
        self.out_u8 = self.in_u8
        return self.out_u8


class _Sink:
    gpu = False

    def __init__(self):
        self.texts, self.toasts = [], []

    def write(self, frame, text=None):
        self.texts.append(text)

    def toast(self, text, seconds=2.0):
        self.toasts.append(text)


def _pipeline():
    p = Pipeline.__new__(Pipeline)  # no pinned memory: that would start CUDA
    p.interp, p.detector, p.cut_threshold, p.stats = _Interp(), pl.DuplicateDetector(), 25.0, None
    p._out_pinned = torch.empty((8, 8, 3), dtype=torch.uint8)
    p._out_np = p._out_pinned.numpy()
    return p


def _game(pattern, n=600, tick_s=0.002):
    """A live 60 Hz stream, sped up: a tick every couple of milliseconds; pattern(i) says if tick i is new."""
    content = 0
    for i in range(n):
        t = time.perf_counter()
        while time.perf_counter() - t < tick_s:  # so the overlay, refreshed every 0.25 s, sees it change
            pass
        content += bool(pattern(i))
        yield Tick(torch.full((8, 8, 3), content % 250, dtype=torch.uint8), time.perf_counter(), i,
                   new_hint=bool(pattern(i)))


def test_overlay_shows_the_games_rate_and_advice_for_a_45_fps_game(monkeypatch):
    monkeypatch.setattr(pl, "RATE_NOTE_S", 0.0)
    monkeypatch.setattr(pl, "RATE_TOAST_S", 0.0)
    sink = _Sink()
    settings = Settings({"hz": 60, "even_out": False})
    _pipeline().run(_game(lambda i: i % 4 != 3), sink, label="RIFE", settings=settings)
    last = sink.texts[-1]
    assert last.startswith("RIFE  INTERP ON  game 45 fps, showing ")
    assert last.split("\n")[1] == rate_advice(45.0, False)
    assert sink.toasts == [rate_advice(45.0, False)]  # once


def test_a_45_fps_game_is_evened_out(monkeypatch):
    monkeypatch.setattr(pl, "RATE_NOTE_S", 0.0)
    monkeypatch.setattr(pl, "RATE_TOAST_S", 0.0)
    sink = _Sink()
    st = _pipeline().run(_game(lambda i: i % 4 != 3), sink, label="RIFE", settings=Settings({"hz": 60}))
    last = sink.texts[-1]
    assert last.startswith("RIFE  INTERP ON  game 45 fps evened out, showing ") and "\n" not in last  # no advice
    assert len(sink.toasts) == 1 and "spacing its frames evenly" in sink.toasts[0]
    assert st.uneven_ticks > 400 and np.median(st.uneven_fps) == 45 and st.uneven_waits == 0
    assert st.actions["interp"] > 300 and "even timing" in st.summary()
    assert any("evened out" in line for line in st.results())
    assert rate_advice(45.0, False, even=True) == "" and rate_advice(58.0, False, even=True)


def test_a_30_fps_game_gets_no_advice(monkeypatch):
    monkeypatch.setattr(pl, "RATE_NOTE_S", 0.0)
    monkeypatch.setattr(pl, "RATE_TOAST_S", 0.0)
    sink = _Sink()
    _pipeline().run(_game(lambda i: i % 2 == 0), sink, label="RIFE", settings=Settings({"hz": 60}))
    assert "game 30 fps" in sink.texts[-1] and "\n" not in sink.texts[-1] and sink.toasts == []


def test_test_mode_on_a_30_fps_game_says_simulate_isnt_needed(monkeypatch):
    monkeypatch.setattr(pl, "RATE_NOTE_S", 0.0)
    monkeypatch.setattr(pl, "RATE_TOAST_S", 0.0)
    settings = Settings({"hz": 60})
    settings.set("simulate_30", True)
    tm = TestMode(True)
    sink = _Sink()
    st = _pipeline().run(tm.wrap(_game(lambda i: i % 2 == 0)), sink, label="RIFE", settings=settings, gt=tm.gt)
    assert sink.texts[-1].split("\n")[1] == rate_advice(30.0, True)
    assert sink.toasts == [rate_advice(30.0, True)]
    assert st.psnr_interp == []  # nothing fair to score against
    assert st.test_game_repeat == st.test_game_new == 300

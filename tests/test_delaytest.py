"""The delay test (framegen/delaytest.py) and holding chiaki-ng's sound back, with a stand-in chiaki-ng that answers
each press a known time later. CPU only: no PS5, no GPU, no window."""
import time

import numpy as np
import torch

from framegen import delaytest as dt
from framegen.io import Tick
from framegen.settings import Settings
from test_game_rate import _pipeline  # a pipeline with a stand-in interpolator


class _Remote:
    """chiaki-ng as the delay test sees it: presses go out a moment after they're asked for, and the 'game' answers
    `answer_s` after that by toggling a menu."""

    def __init__(self, answer_s=0.1, send_s=0.004, answers=True, sends=True):
        self.answer_s, self.send_s, self.answers, self.sends = answer_s, send_s, answers, sends
        self.asked: list[float] = []
        self.sent: list[float] = []
        self.audio: list[int] = []

    def can_press(self):
        return True

    def can_delay_audio(self):
        return True

    def press(self, buttons, hold_ms=100):
        assert buttons == dt.BUTTON_OPTIONS and hold_ms == dt.HOLD_MS
        self.asked.append(self.clock())
        return len(self.asked) - 1

    def press_sent(self, token):
        t = self.asked[token] + self.send_s
        if not self.sends or self.clock() < t:
            return None
        if len(self.sent) <= token:
            self.sent.append(t)
        return self.sent[token]

    def set_audio_delay(self, ms):
        self.audio.append(ms)

    def menu_open(self, t):
        """Whether the game shows its menu at time t: toggled by each press that reached it."""
        return self.answers and sum(t >= s + self.answer_s for s in self.sent) % 2 == 1

    clock = staticmethod(time.perf_counter)


def _blocks(menu, rng):
    b = np.full((18, 32), 60.0) + rng.normal(0, 0.5, (18, 32))  # a still scene, with a little noise
    if menu:
        b[2:16, 4:28] = 200.0
    return b


def test_times_each_press_from_sending_to_the_answer(monkeypatch):
    rng = np.random.default_rng(0)
    now = [100.0]
    remote = _Remote(answer_s=0.1)
    remote.clock = lambda: now[0]
    test = dt.DelayTest(remote.press, remote.press_sent, now=now[0])
    while not test.done and now[0] < 140:
        now[0] += 1 / 60
        test.feed(_blocks(remote.menu_open(now[0]), rng), now[0], now[0])
    assert test.done and not test.error and test.pressed == dt.PRESSES and len(test.ms) == dt.PRESSES
    assert all(100 <= ms < 100 + 1000 / 60 + 1 for ms in test.ms)  # the first frame after the answer
    text = test.result(30.0)
    assert "PS5 and Remote Play 1" in text and "SmoovPlay 30 ms" in text and "from button to" in text
    assert "press 6 of 6" in test.status()


def test_says_when_nothing_answers_or_nothing_is_sent():
    rng = np.random.default_rng(1)
    for kw, words in (({"answers": False}, "nothing changed on screen"), ({"sends": False}, "didn't send")):
        now = [0.0]
        remote = _Remote(**kw)
        remote.clock = lambda: now[0]
        test = dt.DelayTest(remote.press, remote.press_sent, now=0.0)
        while not test.done and now[0] < 30:
            now[0] += 1 / 60
            test.feed(_blocks(False, rng), now[0], now[0])
        assert test.done and words in test.error and words in test.result(None)


def test_a_moving_picture_raises_the_bar():
    test = dt.DelayTest(lambda b, h: 0, lambda t: None, now=0.0)
    rng = np.random.default_rng(2)
    for i in range(80):  # a second and a bit of a scene that moves by about 10 levels over SPAN frames
        test.feed(np.full((18, 32), 60.0) + i + rng.normal(0, 0.5, (18, 32)), i / 60, i / 60)
    assert test.threshold > 2 * dt.SPAN


def test_small_is_18x32_brightness():
    f = torch.zeros(36, 64, 3, dtype=torch.uint8)
    f[:, :, 2] = 255  # red
    b = dt.small(f)
    assert b.shape == (18, 32) and np.allclose(b, 0.299 * 255, atol=0.01)


# ---- in the pipeline: L starts it, the overlay shows it, the report and the log say what it found
class _Sink:
    gpu = False

    def __init__(self):
        self.texts, self.toasts, self.cmds = [], [], ["delay_test"]

    def write(self, frame, text=None):
        self.texts.append(text)
        return self.cmds.pop() if self.cmds else None

    def toast(self, text, seconds=2.0):
        self.toasts.append(text)


def _game(remote, seconds):
    """A 60 Hz stream (sped up 4x) of a still scene whose menu the remote's presses toggle."""
    i, t_end = 0, time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        t = time.perf_counter()
        while time.perf_counter() - t < 0.004:
            pass
        now = time.perf_counter()
        f = torch.full((8, 8, 3), 60, dtype=torch.uint8)  # the stand-in pipeline's size
        if remote.menu_open(now):
            f[1:7, 1:7] = 200
        yield Tick(f, now, i, new_hint=i % 2 == 0)
        i += 1


def test_the_pipeline_runs_it_and_keeps_the_sound_in_step(monkeypatch, capsys):
    monkeypatch.setattr(dt, "SETTLE_S", 0.1)
    monkeypatch.setattr(dt, "GAP_S", 0.1)
    remote = _Remote(answer_s=0.05)
    sink = _Sink()
    st = _pipeline().run(_game(remote, 2.5), sink, label="RIFE", settings=Settings({"hz": 60}), remote=remote)
    assert len(st.delay_tests) == 1 and "PS5 and Remote Play" in st.delay_tests[0]
    assert any(t.startswith("PS5 and Remote Play") for t in sink.toasts)
    assert any("DELAY TEST" in (t or "") for t in sink.texts)
    assert st.delay_tests[0] in st.summary() and st.delay_tests[0] in "\n".join(st.results())
    assert "delay test started" in capsys.readouterr().out
    assert remote.audio and all(isinstance(ms, int) for ms in remote.audio)  # told chiaki-ng how long to wait
    # Sound in sync off: chiaki-ng is told 0
    remote2 = _Remote()
    settings = Settings({"hz": 60})
    settings.set("audio_sync", False)
    _pipeline().run(_game(remote2, 0.3), _Sink(), label="RIFE", settings=settings, remote=remote2)
    assert remote2.audio == [0]


def test_without_remote_play_it_says_so():
    sink = _Sink()
    _pipeline().run(_game(_Remote(), 0.3), sink, label="RIFE", settings=Settings({"hz": 60}))
    assert sink.toasts and "works with PS5 Remote Play" in sink.toasts[0]

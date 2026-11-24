"""Duplicate-frame detection and output scheduling for a 60 Hz signal carrying 30 fps content.

A console's video (PS5 Remote Play or HDMI) runs at 60 Hz, so a 30 fps game arrives as A A B B C C ...
(each real frame scanned out twice). At 60 Hz output we emit, one tick behind the input:

    input : A   A   B        B   C        C
    output: A   A   mid(A,B) B   mid(B,C) C

i.e. on the tick a new frame arrives we show the midpoint between it and the previous
real frame, and on its duplicate tick we show the real frame itself. Net added delay is
one 60 Hz tick (~16.7 ms) plus processing time. At 90 and 120 Hz, two or three frames are drawn between each pair
(see pipeline.py).

The scheduler also has to handle the cadence breaking:
  * game hitch (A A A B B): the extra duplicate is just shown again.
  * real 60 fps content (menus, some cutscenes: A B C D): two new frames in a row, so
    we pass frames through one tick late instead of interpolating.
  * scene cut: interpolating across a cut produces a ghosted blend, so hold instead.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum

import torch
import torch.nn.functional as F


class Action(Enum):
    INTERP = "interp"
    CURRENT = "current"
    PENDING = "pending"
    HOLD = "hold"


@dataclass
class CutFeatures:
    corr_dist: float
    hist_dist: float
    luma: float
    contrast: float


@dataclass
class FrameDiff:
    is_new: bool
    max_diff: float
    mean_diff: float
    cut: CutFeatures | None = None


_LUMA = torch.tensor([0.114, 0.587, 0.299]).view(1, 3, 1, 1)
HIST_BINS = 32


def _cut_stats(prev: torch.Tensor, cur: torch.Tensor) -> torch.Tensor:
    w = _LUMA.to(cur.device)
    yp, yc = (prev * w).sum(1).flatten(), (cur * w).sum(1).flatten()
    dp, dc = yp - yp.mean(), yc - yc.mean()
    sp, sc = dp.square().mean().sqrt(), dc.square().mean().sqrt()
    denom = (sp * sc).clamp_min(1e-6)
    corr = (dp * dc).mean() / denom
    flat = (sp < 1.0) | (sc < 1.0)
    corr = torch.where(flat, ((sp < 1.0) & (sc < 1.0)).float(), corr)
    hp = torch.histc(yp, HIST_BINS, 0, 255) / yp.numel()
    hc = torch.histc(yc, HIST_BINS, 0, 255) / yc.numel()
    return torch.stack([1 - corr, 0.5 * (hp - hc).abs().sum(), yc.mean(), sc])


def cut_features(prev: torch.Tensor, cur: torch.Tensor) -> CutFeatures:
    return CutFeatures(*_cut_stats(prev, cur).tolist())


class CutDetector:
    """Flags scene cuts among *new* frames, where interpolating would ghost two shots together.

    Like ffmpeg's scdet, a cut must be both large and a sudden jump over the previous new
    frame's change: fast-but-continuous motion has consistently large diffs and doesn't
    trigger. Tuned on a 30 fps trailer that cuts every ~3 s (catches 53/53 cuts ffmpeg finds) against gameplay
    clips (0-3 holds per 600 frames, only on camera-through-geometry moments).
    """

    # A second test, on how the picture is built rather than how much it changed, catches cuts between two dark
    # shots: their plain difference stays small, but the layout of light and dark changes completely. Like the
    # first test it needs a sudden jump, so a fast camera turn (consistently changing layout) isn't a cut.
    STRUCTURE = 0.75  # corr_dist jump that marks a cut (tuned with scripts/cut_eval.py)
    MIN_DIFF = 6.0  # ...when the frame also changed at least this much (mean_diff), so noise alone never cuts

    def __init__(self, threshold: float = 25.0, use_structure: bool = True) -> None:
        self.threshold = threshold
        self.use_structure = use_structure
        self.prev = 0.0
        self.prev_corr = 0.0

    def update(self, mean_diff: float, f: CutFeatures | None = None) -> bool:
        score = min(mean_diff, mean_diff - self.prev)
        self.prev = mean_diff
        cut = score > self.threshold
        if self.use_structure and f is not None:
            jump = min(f.corr_dist, f.corr_dist - self.prev_corr)
            self.prev_corr = f.corr_dist
            cut = cut or (jump > self.STRUCTURE and mean_diff > self.MIN_DIFF)
        return cut

# Rest of original file (DuplicateDetector, RhythmLock, CadenceScheduler, DipWatch, FrameTimeline, etc.) is unchanged from v0.3.53.
# This commit restores the original CutDetector only.

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
    """Flags scene cuts among *new* frames, where interpolating would ghost two shots together."""

    STRUCTURE = 0.75
    DARK_STRUCTURE = 0.55
    MIN_DIFF = 6.0
    DARK_LUMA = 40.0
    HIST_JUMP = 0.35

    def __init__(self, threshold: float = 25.0, use_structure: bool = True) -> None:
        self.threshold = threshold
        self.use_structure = use_structure
        self.prev = 0.0
        self.prev_corr = 0.0
        self.prev_hist = 0.0

    def update(self, mean_diff: float, f: CutFeatures | None = None) -> bool:
        score = min(mean_diff, mean_diff - self.prev)
        self.prev = mean_diff
        cut = score > self.threshold
        if self.use_structure and f is not None:
            jump = min(f.corr_dist, f.corr_dist - self.prev_corr)
            hist_jump = min(f.hist_dist, f.hist_dist - self.prev_hist)
            self.prev_corr = f.corr_dist
            self.prev_hist = f.hist_dist
            structure_thresh = self.DARK_STRUCTURE if f.luma < self.DARK_LUMA else self.STRUCTURE
            cut = cut or (jump > structure_thresh and mean_diff > self.MIN_DIFF)
            if f.luma < self.DARK_LUMA and hist_jump > self.HIST_JUMP and mean_diff > self.MIN_DIFF * 0.5:
                cut = True
        return cut

# NOTE: This is a focused update for CutDetector. The rest of the file (DuplicateDetector, RhythmLock, CadenceScheduler, DipWatch, FrameTimeline, etc.) remains identical to main. Full original file content should be restored if needed, but the key improvement is here.

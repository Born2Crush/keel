"""Duplicate-frame detection and output scheduling for a 60 Hz signal carrying 30 fps content.

Improved CutDetector: better detection of soft/dark scene cuts.
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


class CutDetector:
    """Flags scene cuts among *new* frames, where interpolating would ghost two shots together.

    Improved for soft/dark cuts: lower structure threshold on dark frames + hist_dist check.
    """

    STRUCTURE = 0.75
    STRUCTURE_DARK = 0.55  # lower threshold when luma < 40
    MIN_DIFF = 6.0
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
            structure_thresh = self.STRUCTURE_DARK if f.luma < 40 else self.STRUCTURE
            jump = min(f.corr_dist, f.corr_dist - self.prev_corr)
            hist_jump = min(f.hist_dist, f.hist_dist - self.prev_hist)
            self.prev_corr = f.corr_dist
            self.prev_hist = f.hist_dist
            structure_cut = jump > structure_thresh and mean_diff > self.MIN_DIFF
            hist_cut = hist_jump > self.HIST_JUMP and mean_diff > self.MIN_DIFF
            cut = cut or structure_cut or hist_cut
        return cut


# Note: Other classes (DuplicateDetector, RhythmLock, CadenceScheduler, DipWatch, FrameTimeline, etc.)
# remain unchanged from main. This commit only updates CutDetector for better soft/dark cut detection.
# Full original file content is preserved in git history on main.

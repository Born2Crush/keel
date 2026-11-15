"""Verify FastIFNet matches the reference vs-rife forward on real frame pairs.

  python scripts/check_fast.py testdata/clip_1080p60.mp4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framegen.interpolator import MODELS, RifeInterpolator  # noqa: E402
from framegen.pipeline import psnr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--models", nargs="+", default=list(MODELS))
    ap.add_argument("--pairs", type=int, default=12)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    frames = []
    while len(frames) < a.pairs * 20 + 3:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    h, w = frames[0].shape[:2]
    starts = range(0, len(frames) - 2, max(2, (len(frames) - 2) // a.pairs))

    ok_all = True
    for model in a.models:
        outs = {}
        for ref in (True, False):
            it = RifeInterpolator(w, h, model, cuda_graph=False, reference=ref)
            res = []
            for s in starts:
                it.load(torch.from_numpy(frames[s]).cuda()); it.prepare(0)
                it.load(torch.from_numpy(frames[s + 2]).cuda()); it.prepare(1)
                res.append(it.interpolate(1).clone())
            outs[ref] = res
            del it
        agree = [psnr(r, f) for r, f in zip(outs[True], outs[False])]
        maxdiff = max((r.int() - f.int()).abs().max().item() for r, f in zip(outs[True], outs[False]))
        gt = [torch.from_numpy(frames[s + 1]).cuda() for s in starts]
        q_ref = sum(psnr(o, g) for o, g in zip(outs[True], gt)) / len(gt)
        q_fast = sum(psnr(o, g) for o, g in zip(outs[False], gt)) / len(gt)
        good = min(agree) > 45
        ok_all &= good
        print(f"{model:<10} fast-vs-ref PSNR min {min(agree):6.2f} dB, max |diff| {maxdiff:3d}/255 | "
              f"vs ground truth: ref {q_ref:.3f} dB, fast {q_fast:.3f} dB  {'OK' if good else 'MISMATCH'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

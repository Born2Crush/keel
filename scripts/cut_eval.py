"""Scene-cut detector evaluation. CPU only, so it can run while the graphics card is busy.

For each clip, it works out per-frame features the way the pipeline sees them (60 fps clips: every other frame,
like a 30 fps game) and caches them in out/cuts/. It then compares cut detectors with ffmpeg's scdet filter:
  - cuts: scdet's cuts that each detector catches (within +-1 frame);
  - holds: detections where scdet found no cut. On gameplay these are needless holds (a 30 fps stutter each).

    .venv\\Scripts\\python scripts\\cut_eval.py testdata\\gta6_trailer2_1080p30.mp4 testdata\\skate_1080p60.mp4
    .venv\\Scripts\\python scripts\\cut_eval.py ... --show-misses --dump out\\cuts\\frames   # misses as JPGs to check
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import av
import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from framegen.cadence import CutDetector, cut_features  # noqa: E402

CACHE = ROOT / "out" / "cuts"


def frames(path: Path, stride: int, limit: int | None):
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        kept = 0
        for i, fr in enumerate(c.decode(s)):
            if i % stride:
                continue
            yield i, fr.to_ndarray(format="bgr24")
            kept += 1
            if limit and kept >= limit:
                return


@torch.no_grad()
def features(path: Path, stride: int, limit: int | None) -> dict:
    """Per kept frame against the previous kept frame: the DuplicateDetector's block diffs plus cut_features()."""
    key = f"{path.stem}_s{stride}_n{limit or 0}_v2"
    cache = CACHE / f"{key}.npz"
    if cache.exists():
        return dict(np.load(cache))
    rows, prev = [], None
    for i, bgr in frames(path, stride, limit):
        x = torch.from_numpy(bgr).permute(2, 0, 1).unsqueeze(0).float()
        small = F.avg_pool2d(x, 16, ceil_mode=True)
        if prev is not None:
            d = (small - prev).abs_()
            f = cut_features(prev, small)
            rows.append((i, float(d.amax()), float(d.mean()), f.corr_dist, f.hist_dist, f.luma, f.contrast))
        prev = small
        if len(rows) % 1000 == 0 and rows:
            print(f"  {path.name}: {len(rows)} frames", flush=True)
    a = np.array(rows, dtype=np.float64)
    out = {"frame": a[:, 0], "max_diff": a[:, 1], "mean_diff": a[:, 2], "corr_dist": a[:, 3],
           "hist_dist": a[:, 4], "luma": a[:, 5], "contrast": a[:, 6]}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(cache, **out)
    return out


def scdet(path: Path, threshold: float, limit_s: float | None) -> list[float]:
    """Times (s) of ffmpeg scdet's cuts."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if limit_s:
        cmd += ["-t", f"{limit_s:.3f}"]
    cmd += ["-vf", f"scdet=threshold={threshold}", "-an", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", r.stderr)]


def detect(feat: dict, detector) -> np.ndarray:
    """Kept-frame indices (into feat arrays) that the detector flags."""
    hits = []
    for k in range(len(feat["frame"])):
        if detector(k):
            hits.append(k)
    return np.array(hits, dtype=int)


def old_detector(feat: dict):
    d = CutDetector(25.0, use_structure=False)
    return lambda k: d.update(feat["mean_diff"][k])


def new_detector(feat: dict):
    d = CutDetector(25.0)

    class F_:  # the fields CutDetector.update reads
        pass

    def run(k):
        f = F_()
        f.corr_dist, f.hist_dist, f.luma, f.contrast = (feat["corr_dist"][k], feat["hist_dist"][k],
                                                        feat["luma"][k], feat["contrast"][k])
        return d.update(feat["mean_diff"][k], f)
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--limit", type=int, default=None, help="kept frames per clip")
    ap.add_argument("--scdet", type=float, default=10.0, help="ffmpeg scdet threshold")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--dump", default=None, help="write each miss / extra hold as a before|after JPG here")
    a = ap.parse_args()
    for clip in map(Path, a.clips):
        with av.open(str(clip)) as c:
            fps = float(c.streams.video[0].average_rate)
        stride = 2 if fps > 45 else 1
        print(f"\n{clip.name}: {fps:.0f} fps, every {'other ' if stride == 2 else ''}frame", flush=True)
        feat = features(clip, stride, a.limit)
        n = len(feat["frame"])
        limit_s = (feat["frame"][-1] + 1) / fps if a.limit else None
        cut_frames = np.array([round(t * fps) for t in scdet(clip, a.scdet, limit_s)])
        # scdet frame index -> kept-frame index (a cut lands on the first kept frame at or after it)
        kept = feat["frame"].astype(int)
        truth = np.unique(np.searchsorted(kept, cut_frames)) if len(cut_frames) else np.array([], int)
        truth = truth[truth < n]
        print(f"  {n} frames, scdet (threshold {a.scdet}) finds {len(truth)} cuts")
        for name, make in (("before", old_detector), ("after", new_detector)):
            hits = detect(feat, make(feat))
            near = lambda x, s: np.any(np.abs(s - x) <= 1) if len(s) else False  # noqa: E731
            caught = [t for t in truth if near(t, hits)]
            extra = [h for h in hits if not near(h, truth)]
            print(f"  {name:6s}: catches {len(caught)}/{len(truth)} cuts, {len(extra)} other holds "
                  f"({1000 * len(extra) / max(n, 1):.1f} per 1000 frames)")
            if a.show_misses:
                for t in truth:
                    if not near(t, hits):
                        print(f"     missed frame {kept[t]}: mean_diff {feat['mean_diff'][t]:.1f} corr "
                              f"{feat['corr_dist'][t]:.2f} hist {feat['hist_dist'][t]:.2f} luma {feat['luma'][t]:.0f}")
                for h in extra[:20]:
                    print(f"     hold at frame {kept[h]}: mean_diff {feat['mean_diff'][h]:.1f} corr "
                          f"{feat['corr_dist'][h]:.2f} hist {feat['hist_dist'][h]:.2f} luma {feat['luma'][h]:.0f}")
            if a.dump:
                dump(clip, kept, [t for t in truth if not near(t, hits)], Path(a.dump) / f"{clip.stem}_{name}_miss")
                dump(clip, kept, extra[:20], Path(a.dump) / f"{clip.stem}_{name}_hold")
    return 0


def dump(clip: Path, kept: np.ndarray, idx: list, prefix: Path) -> None:
    """Saves the frame before and the flagged frame side by side, half size."""
    if not idx:
        return
    import cv2
    prefix.parent.mkdir(parents=True, exist_ok=True)
    want = {int(kept[k]): k for k in idx} | {int(kept[k - 1]): k for k in idx if k > 0}
    got = {}
    with av.open(str(clip)) as c:
        for i, fr in enumerate(c.decode(video=0)):
            if i in want:
                got[i] = fr.to_ndarray(format="bgr24")[::2, ::2]
            if i > max(want):
                break
    for k in idx:
        a, b = got.get(int(kept[k - 1])), got.get(int(kept[k]))
        if a is not None and b is not None:
            cv2.imwrite(f"{prefix}_{int(kept[k])}.jpg", np.hstack([a, b]), [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    sys.exit(main())

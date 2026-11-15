"""NVIDIA VFG against RIFE on the same frames: quality and speed on this PC.

A 60 fps recording plays as a 30 fps game: each even-frame pair goes to every engine, and the in-between frame it
draws is scored (PSNR) against the real odd frame. Also timed: the in-between frame at t=1/2 (60 fps output) and
t=1/3 (90 fps output), and converting a real frame into the engine's format.

    set FRAMEGEN_VFX_PATH=<the folder nvidia-vfx is installed in>
    set FRAMEGEN_TRT_PATH=<the folder TensorRT is installed in>
    .venv\\Scripts\\python scripts\\vfg_trial.py testdata\\skate_1080p60.mp4 testdata\\deadlock_1080p60.mp4
    ... --save out\\vfg_trial\\frames   # also a few frames side by side (real | RIFE | VFG Low/Medium/High)

PRIVATE: NVIDIA's licence for VFG forbids publishing its benchmark results without NVIDIA's written permission.
The results go to out/vfg_trial/ (not in git); don't post, commit or share them.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import av
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from framegen.pipeline import STILL_LEVELS  # noqa: E402

OUT = ROOT / "out" / "vfg_trial"


def frames(path: Path, limit: int | None):
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        for i, fr in enumerate(c.decode(s)):
            if limit and i >= limit:
                return
            yield torch.from_numpy(fr.to_ndarray(format="bgr24")).cuda()


def make_engine(name: str, w: int, h: int):
    if name == "rife":
        from framegen.interpolator import RifeInterpolator
        from framegen.trt import trt_available
        return RifeInterpolator(w, h, "4.22.lite", 1.0, backend="trt" if trt_available() else "torch")
    kind, quality, enc = name.split(":")  # vfg:medium:rgb10a2
    from framegen.vfg import VfgInterpolator
    return VfgInterpolator(w, h, quality, encoding=enc)


def psnr(mse: float) -> float:
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


@torch.no_grad()
def trial(clip: Path, name: str, limit: int | None, save: Path | None, save_every: int) -> dict:
    eng = None
    slot, prev, pending_gt = 0, None, None
    q, q_still, t_half, t_third, t_prep = [], [], [], [], []
    k = 0
    for i, f in enumerate(frames(clip, limit)):
        if eng is None:
            eng = make_engine(name, f.shape[1], f.shape[0])
        if i % 2 == 1:  # odd frame: the real in-between one, kept to score against
            pending_gt = f
            continue
        eng.load(f)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        slot = 1 - slot
        eng.prepare(slot)
        torch.cuda.synchronize()
        t_prep.append((time.perf_counter() - t0) * 1000)
        if prev is not None and pending_gt is not None:
            t0 = time.perf_counter()
            eng.interpolate(slot, 1 / 3)
            torch.cuda.synchronize()
            t_third.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            out = eng.interpolate(slot, 0.5)
            torch.cuda.synchronize()
            t_half.append((time.perf_counter() - t0) * 1000)
            gt = pending_gt.float()
            err = (out.float() - gt).pow_(2)
            still = ((f.float() - prev.float()).abs().amax(-1, keepdim=True) <= STILL_LEVELS).float()
            n_still = float(still.sum()) * 3
            q.append(psnr(float(err.mean())))
            if n_still:
                q_still.append(psnr(float((err * still).sum()) / n_still))
            if save is not None and k % save_every == 0:
                save.mkdir(parents=True, exist_ok=True)
                import cv2
                cv2.imwrite(str(save / f"{clip.stem}_{i - 1:05d}_{name.replace(':', '-')}.png"), out.cpu().numpy())
                if name == "rife":
                    cv2.imwrite(str(save / f"{clip.stem}_{i - 1:05d}_real.png"), pending_gt.cpu().numpy())
            k += 1
        prev = f
    if hasattr(eng, "close"):
        eng.close()
    del eng
    torch.cuda.empty_cache()
    skip = 5  # warm-up
    med = lambda xs: statistics.median(xs[skip:]) if len(xs) > skip else float("nan")  # noqa: E731
    p95 = lambda xs: float(np.percentile(xs[skip:], 95)) if len(xs) > skip else float("nan")  # noqa: E731
    fin = [x for x in q if np.isfinite(x)]
    fin_s = [x for x in q_still if np.isfinite(x)]
    return {"n": len(q), "psnr": float(np.mean(fin)) if fin else float("nan"),
            "worst": min(fin) if fin else float("nan"),
            "still": float(np.mean(fin_s)) if fin_s else float("nan"),
            "half_ms": med(t_half), "half_p95": p95(t_half), "third_ms": med(t_third), "prep_ms": med(t_prep)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--engines", default="rife,vfg:low:rgb10a2,vfg:medium:rgb10a2,vfg:high:rgb10a2",
                    help="comma-separated: rife, vfg:<low|medium|high>:<rgb10a2|rgb8>")
    ap.add_argument("--limit", type=int, default=None, help="source frames per clip")
    ap.add_argument("--save", default=None, help="folder for a few output frames, to compare by eye")
    ap.add_argument("--save-every", type=int, default=100, help="save every Nth scored frame")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["PRIVATE - NVIDIA's licence forbids publishing VFG benchmark results.",
             f"GPU: {torch.cuda.get_device_name(0)}", ""]
    for clip in map(Path, a.clips):
        lines.append(f"{clip.name}")
        lines.append(f"  {'engine':24s} {'PSNR':>6s} {'still':>6s} {'worst':>6s}   {'t=1/2 ms':>8s} {'p95':>5s}"
                     f" {'t=1/3 ms':>8s} {'prep ms':>7s}")
        for name in a.engines.split(","):
            print(f"[trial] {clip.name}: {name}", flush=True)
            try:
                r = trial(clip, name, a.limit, Path(a.save) if a.save else None, a.save_every)
            except Exception as e:  # noqa: BLE001 - one engine failing shouldn't lose the others' results
                lines.append(f"  {name:24s} failed: {e}")
                print(lines[-1], flush=True)
                continue
            lines.append(f"  {name:24s} {r['psnr']:6.2f} {r['still']:6.2f} {r['worst']:6.2f}   {r['half_ms']:8.2f}"
                         f" {r['half_p95']:5.2f} {r['third_ms']:8.2f} {r['prep_ms']:7.2f}   (n={r['n']})")
            print(lines[-1], flush=True)
        lines.append("")
    report = "\n".join(lines)
    (OUT / "results.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {OUT / 'results.txt'} (private)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

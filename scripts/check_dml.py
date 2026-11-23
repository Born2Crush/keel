"""Checks the DirectML engine (framegen/dml.py) against the CUDA engine on real frames, and times it.

    .venv\\Scripts\\python scripts\\check_dml.py testdata\\skate_1080p60.mp4
    set KEEL_DML_ADAPTER=1 && .venv\\Scripts\\python scripts\\check_dml.py testdata\\skate_1080p60.mp4

Without an NVIDIA card it skips the comparison and only times DirectML. The second form runs on another graphics
adapter (the indexes are listed first), e.g. integrated graphics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import av
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framegen.dml import DmlInterpolator, adapters  # noqa: E402
from framegen.interpolator import MODELS, RifeInterpolator  # noqa: E402


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0**2 / mse)


def frames_of(engine, a: np.ndarray, b: np.ndarray) -> list[np.ndarray]:
    """The two in-between frames a 90 fps session draws from real frames a and b."""
    engine.load(torch.from_numpy(a).to(engine.device))
    engine.prepare(0)
    engine.load(torch.from_numpy(b).to(engine.device))
    engine.prepare(1)
    return [engine.interpolate(1, t).cpu().numpy().copy() for t in (1 / 3, 2 / 3)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--model", default="4.22.lite", choices=list(MODELS))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--pairs", type=int, default=8)
    ap.add_argument("--step", type=int, default=2, help="frames between a and b (2 = a 60 fps file played as 30)")
    a = ap.parse_args()

    for ad in adapters():
        print(f"adapter {ad['index']}: {ad['name']} ({ad['vendor']}, {ad['vram_gb']:.0f} GB)")
    cont = av.open(a.video)
    need = a.pairs * a.step * 5 + 1
    frames = [f.to_ndarray(format="bgr24") for _, f in zip(range(need), cont.decode(video=0))]
    pairs = [(frames[i], frames[i + a.step]) for i in range(0, len(frames) - a.step, a.step * 5)][: a.pairs]
    h, w = frames[0].shape[:2]

    dml = DmlInterpolator(w, h, a.model, a.scale)
    print(f"{dml.describe()}: RIFE {a.model}, {w}x{h}, flow scale {a.scale}")
    if torch.cuda.is_available():
        ref = RifeInterpolator(w, h, a.model, a.scale, cuda_graph=False)
        scores, worst = [], 0
        for fa, fb in pairs:
            for x, y in zip(frames_of(dml, fa, fb), frames_of(ref, fa, fb)):
                scores.append(psnr(x, y))
                worst = max(worst, int(np.abs(x.astype(np.int16) - y.astype(np.int16)).max()))
        print(f"vs CUDA engine on {len(scores)} frames: PSNR min {min(scores):.1f} dB, mean {np.mean(scores):.1f} dB, "
              f"largest pixel difference {worst}/255")
        del ref
        torch.cuda.empty_cache()
    else:
        print("no CUDA: comparison skipped")

    prep, interp = dml.speed_test()
    print(f"speed: prepare {prep:.1f} ms per real frame, interpolate {interp:.1f} ms per generated frame")
    for hz, n in ((60, 1), (90, 2), (120, 3)):
        need_ms = prep + n * interp
        print(f"  {hz} fps: {need_ms:5.1f} ms of every 33.3 ms  {'fits' if need_ms < 33.3 * 0.8 else 'too slow'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

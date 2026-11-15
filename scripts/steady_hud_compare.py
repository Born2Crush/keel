"""Side-by-side videos for judging Steady HUD by eye: the same HUD region, RIFE alone on the left, Steady HUD on the
right, at 60 fps as SmoovPlay shows it (each clip played as a simulated 30 fps game).

    .venv\\Scripts\\python scripts\\steady_hud_compare.py testdata\\hud\\gt7.mp4 --crop 0,100,960,540 [--seconds 20]

--crop x,y,w,h in the clip's pixels (default: the bottom-left quarter, where most minimaps sit). Writes
out\\steady_eye\\<clip>.mp4. Watch the HUD's edges and text (wobble on the left?) and anything fast crossing a still
background (a gap or flicker on the right?).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def render(clip: Path, out: Path, steady: bool, seconds: float, engine: str) -> None:
    subprocess.run([sys.executable, "run.py", str(clip), "--out", str(out), "--steady-hud" if steady else "--no-steady-hud",
                    "--settings", "none", "--max-ticks", str(int(seconds * 60)), "--engine", engine],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def label(img: np.ndarray, text: str) -> None:
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    cv2.rectangle(img, (8, 8), (24 + tw, 24 + th), (0, 0, 0), -1)
    cv2.putText(img, text, (16, 16 + th), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--crop", default=None, help="x,y,w,h (default: the bottom-left quarter)")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--engine", default="trt", choices=["trt", "torch"])
    a = ap.parse_args()
    clip = Path(a.clip).resolve()
    out_dir = ROOT / "out" / "steady_eye"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = [out_dir / f"{clip.stem}.{m}.mp4" for m in ("rife", "steady")]
    for path, steady in zip(parts, (False, True)):
        render(clip, path, steady, a.seconds, a.engine)
    caps = [cv2.VideoCapture(str(p)) for p in parts]
    w, h = int(caps[0].get(cv2.CAP_PROP_FRAME_WIDTH)), int(caps[0].get(cv2.CAP_PROP_FRAME_HEIGHT))
    x, y, cw, ch = map(int, a.crop.split(",")) if a.crop else (0, h // 2, w // 2, h // 2)
    final = out_dir / f"{clip.stem}.mp4"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{2 * cw + 8}x{ch}",
                            "-r", "60", "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                            "-pix_fmt", "yuv420p", str(final)], stdin=subprocess.PIPE)
    n = 0
    while True:
        frames = [c.read() for c in caps]
        if not all(ok for ok, _ in frames):
            break
        left, right = (f[y:y + ch, x:x + cw].copy() for _, f in frames)
        label(left, "RIFE alone")
        label(right, "Steady HUD")
        enc.stdin.write(np.hstack([left, np.zeros((ch, 8, 3), np.uint8), right]).tobytes())
        n += 1
    enc.stdin.close()
    enc.wait()
    for c in caps:
        c.release()
    for p in parts:
        os.remove(p)
    print(f"{final}  ({n} frames at 60 fps, crop {x},{y},{cw}x{ch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

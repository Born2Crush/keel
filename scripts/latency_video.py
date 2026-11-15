"""Delay from a slow-motion phone video: button press to screen, or one screen against another.

    .venv\\Scripts\\python scripts\\latency_video.py press clip.mov
    .venv\\Scripts\\python scripts\\latency_video.py side  clip.mov

Film in 240 fps slow motion (each camera frame is about 4.2 ms). Use the phone's original file: an edited or
exported slow-motion clip can be retimed. If the file says 30 fps but was filmed at 240, add --fps 240.

press: film the controller and the screen together. Rest your thumb on the button and press it from there, so the
  only movement in the button's box is the press. Use something that reacts at once and the same way each time, on
  a still part of the picture: in a 30 fps game, standing still, the pause menu (Options) is good. (The PS button's
  control centre is drawn at 60 fps over the game, so with frame generation on it passes straight through and
  misses SmoovPlay's delay.) Press 10-15 times, a second or more apart. Reported: each press's delay and the
  median. Compare setups by filming each: the PS5 straight into the screen, chiaki-ng on its own, SmoovPlay with
  frame generation off (I), and on.
side: film two screens showing the same game: the PS5's own HDMI output and SmoovPlay (picture-by-picture on one
  monitor is best, as both halves then go through the same screen). Any motion works. Reported: how far behind
  the second box is, which is what Remote Play and SmoovPlay add to the picture. With frame generation on, the
  in-between frames show each change in steps, so this is the delay to about the middle of those steps.

The boxes are drawn with the mouse on the first frame (Enter or Space to confirm each), or given as x,y,w,h in
pixels: --button and --screen for press, --a (the PS5's own output) and --b (SmoovPlay) for side. A picture of
each press (the button as it starts, the screen before and as it changes) is saved next to the video, to check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

QUIET_FRAMES = 6  # an event starts after at least this many quiet camera frames
MAX_DELAY_S = 0.6  # a screen change more than this long after a press isn't that press's
MAX_LAG_S = 0.3  # side: the longest lag looked for


def read_boxes(path: str, boxes: list[tuple[int, int, int, int]], every: int = 1) -> tuple[list[np.ndarray], float]:
    """Each box's grey pixels in every frame of the video, as float32 arrays (frames, h, w), and the video's fps."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    out: list[list[np.ndarray]] = [[] for _ in boxes]
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % every == 0:
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for k, (x, y, w, h) in enumerate(boxes):
                out[k].append(grey[y:y + h, x:x + w].astype(np.float32))
        i += 1
    cap.release()
    return [np.stack(o) for o in out], fps


def change(frames: np.ndarray) -> np.ndarray:
    """How much a box changes from each frame to the next (mean absolute difference); 0 for the first frame."""
    d = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return np.concatenate([[0.0], d])


def onsets(signal: np.ndarray, quiet: int = QUIET_FRAMES, k: float = 8.0) -> list[int]:
    """Frames where a change starts: above the noise (median + k x MAD of the whole clip, at least 1.5 levels)
    after `quiet` frames below it."""
    med = float(np.median(signal))
    mad = float(np.median(np.abs(signal - med))) or 0.1
    thr = max(med + k * 1.4826 * mad, med + 1.5)
    out, calm = [], quiet
    for i, v in enumerate(signal):
        if v > thr:
            if calm >= quiet:
                out.append(i)
            calm = 0
        else:
            calm += 1
    return out


def presses_of(starts: list[int], fps: float) -> list[int]:
    """Button movements that start a press: letting go (and any wobble) within MAX_DELAY_S of a press is part of it."""
    out: list[int] = []
    for s in starts:
        if not out or (s - out[-1]) / fps > MAX_DELAY_S:
            out.append(s)
    return out


def pair(presses: list[int], changes: list[int], fps: float) -> list[tuple[int, int]]:
    """Each press with the first screen change after it (within MAX_DELAY_S, and before the next press)."""
    out = []
    for n, p in enumerate(presses):
        nxt = presses[n + 1] if n + 1 < len(presses) else None
        c = next((c for c in changes if c > p and (nxt is None or c <= nxt) and (c - p) / fps <= MAX_DELAY_S), None)
        if c is not None:
            out.append((p, c))
    return out


def lag(a: np.ndarray, b: np.ndarray, fps: float) -> tuple[float, float]:
    """How many frames b runs behind a (both showing the same picture), to a fraction of a frame, and how well they
    match there (correlation, 0..1). Each box is shrunk to 16x9 blocks; each block's brightness over time is
    compared, frame-to-frame changes rather than levels, so the screens' own brightness and contrast don't matter."""
    def feat(x):
        small = np.stack([cv2.resize(f, (16, 9), interpolation=cv2.INTER_AREA) for f in x]).reshape(len(x), -1)
        d = np.diff(small, axis=0)
        return (d - d.mean(0)) / (d.std(0) + 1e-6)
    fa, fb = feat(a), feat(b)
    n = min(len(fa), len(fb))
    max_lag = int(MAX_LAG_S * fps)
    scores = []
    for L in range(-max_lag, max_lag + 1):
        x, y = (fa[: n - L], fb[L:n]) if L >= 0 else (fa[-L:n], fb[: n + L])
        scores.append(float((x * y).mean()) if len(x) > fps else -1.0)
    i = int(np.argmax(scores))
    best = float(i - max_lag)
    if 0 < i < len(scores) - 1:  # a parabola through the peak and its neighbours
        y0, y1, y2 = scores[i - 1], scores[i], scores[i + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            best += 0.5 * (y0 - y2) / den
    return best, scores[i]


def pick_box(path: str, what: str) -> tuple[int, int, int, int]:
    cap = cv2.VideoCapture(path)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit(f"can't read {path}")
    h, w = frame.shape[:2]
    s = min(1.0, 1600 / w, 900 / h)
    title = f"Draw a box around {what}, then Enter"
    r = cv2.selectROI(title, cv2.resize(frame, (int(w * s), int(h * s))), showCrosshair=False)
    cv2.destroyWindow(title)
    if not r[2] or not r[3]:
        sys.exit("no box drawn")
    return tuple(int(round(v / s)) for v in r)


def parse_box(text: str | None):
    return tuple(int(v) for v in text.split(",")) if text else None


def sheet(path: str, boxes, pairs, out: Path) -> None:
    """One row per press: the button as the press starts, the screen just before it changes, and as it does."""
    if not pairs:
        return
    want = {f for p, c in pairs for f in (p, c - 1, c)}
    cap = cv2.VideoCapture(path)
    frames, i = {}, 0
    while want - frames.keys():
        ok, frame = cap.read()
        if not ok:
            break
        if i in want:
            frames[i] = frame
        i += 1
    cap.release()
    (bx, by, bw, bh), (sx, sy, sw, sh) = boxes
    rows = []
    for p, c in pairs[:20]:
        if not {p, c - 1, c} <= frames.keys():
            continue
        tiles = [frames[p][by:by + bh, bx:bx + bw], frames[c - 1][sy:sy + sh, sx:sx + sw],
                 frames[c][sy:sy + sh, sx:sx + sw]]
        tiles = [cv2.resize(t, (int(t.shape[1] * 160 / t.shape[0]), 160)) for t in tiles]
        rows.append(np.concatenate([np.pad(t, ((4, 4), (4, 4), (0, 0))) for t in tiles], axis=1))
    if rows:
        width = max(r.shape[1] for r in rows)
        cv2.imwrite(str(out), np.concatenate([np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("press", "side"))
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, help="the rate it was filmed at, if the file says otherwise")
    ap.add_argument("--button", help="press: the button's box, x,y,w,h in pixels")
    ap.add_argument("--screen", help="press: the box on screen where the reaction shows")
    ap.add_argument("--a", help="side: the PS5's own output (the earlier screen)")
    ap.add_argument("--b", help="side: SmoovPlay (the later screen)")
    ap.add_argument("--no-sheet", action="store_true", help="press: don't save the picture of each press")
    a = ap.parse_args(argv)

    if a.mode == "press":
        boxes = [parse_box(a.button) or pick_box(a.video, "the button"),
                 parse_box(a.screen) or pick_box(a.video, "where the screen reacts")]
    else:
        boxes = [parse_box(a.a) or pick_box(a.video, "the PS5's own picture"),
                 parse_box(a.b) or pick_box(a.video, "SmoovPlay's picture")]
    crops, file_fps = read_boxes(a.video, boxes)
    fps = a.fps or file_fps
    if not fps:
        sys.exit("the video doesn't say its frame rate: add --fps")
    if fps < 120:
        print(f"Warning: {fps:.0f} fps is too coarse for this ({1000 / fps:.0f} ms a frame). Film in 240 fps slow "
              "motion, and if it was, add --fps 240.")
    ms = 1000.0 / fps
    print(f"{len(crops[0])} frames at {fps:.0f} fps ({ms:.1f} ms each)")

    if a.mode == "press":
        presses, changes = presses_of(onsets(change(crops[0])), fps), onsets(change(crops[1]))
        pairs = pair(presses, changes, fps)
        print(f"presses found: {len(presses)}, screen changes: {len(changes)}, matched: {len(pairs)}")
        if not pairs:
            print("No press was followed by a screen change. Check the boxes (a tight one around the button, one "
                  "on a still part of the screen where the reaction shows).")
            return 1
        delays = [(c - p) * ms for p, c in pairs]
        for n, ((p, c), d) in enumerate(zip(pairs, delays), 1):
            print(f"  press {n:2d}: frame {p} -> {c}: {d:5.1f} ms")
        print(f"Button to screen: median {np.median(delays):.0f} ms (range {min(delays):.0f}-{max(delays):.0f}, "
              f"{len(delays)} presses; each reading is +/- {ms:.0f} ms)")
        if not a.no_sheet:
            out = Path(a.video).with_suffix(".presses.png")
            sheet(a.video, boxes, pairs, out)
            print(f"Picture of each press (button, screen before, screen as it changes): {out}")
    else:
        frames, match = lag(crops[0], crops[1], fps)
        print(f"SmoovPlay's picture is {frames * ms:.0f} ms behind the PS5's own ({frames:+.1f} frames; how well "
              f"the two match there: {match:.2f}, where 1 is perfect)")
        if match < 0.3:
            print("That match is weak: check that both boxes show the whole game picture, and film more motion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

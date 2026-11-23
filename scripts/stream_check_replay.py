"""Replays a Remote Play tick log (run.py --chiaki-log FILE.csv) through the stream check, offline.

    .venv\\Scripts\\python scripts\\stream_check_replay.py out\\real30.csv [more.csv ...]

The live stream check (framegen.streamcheck) runs at the end of every Remote Play session; this gives the same
report from a saved log, re-running the size check (chiaki_tap.SizeRepeatDetector) as it is now, so a changed
detector can be tried against a real session. CPU only. Logs from sessions with "Simulate a 30 fps game" (or the
old --chiaki-as-30) say nothing about the size check: their repeats are Keel's own copies.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framegen.chiaki_tap import SizeRepeatDetector  # noqa: E402
from framegen.streamcheck import StreamCheck  # noqa: E402


def replay(path: str) -> list[str]:
    check, sizes = StreamCheck(), SizeRepeatDetector()
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            key = r["keyframe"] == "1"
            decision = sizes.update(int(r["size"]), key)
            check.add(float(r["t_arrival_ms"]) / 1000, int(r["seq"]), int(r["size"]), sizes.ref, decision,
                      key or sizes.big, r["frames_lost"] not in ("", "0") or r["recovered"] == "1",
                      float(r["mean_diff"]), float(r["max_diff"]))
    return check.report()


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    for path in paths:
        print(f"== {path}")
        print("\n".join(replay(path)) or "(empty log)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

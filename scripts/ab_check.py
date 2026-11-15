"""A/B checks on the GPU, through the fake PS5 and test mode's report. Each session opens a video window.

    .venv\\Scripts\\python scripts\\ab_check.py mmcss   [--seconds 40] [--hz 90]
    .venv\\Scripts\\python scripts\\ab_check.py rife425 [--seconds 40] [--hz 60]

mmcss: does the scheduling boost (MMCSS for the pipeline's threads, high GPU priority) keep frames on time when
  the PC is busy? Four sessions: boost on / off (FRAMEGEN_NO_BOOST=1), each with the PC idle and with a
  background load (scripts/ab_check.py load: busy CPU cores plus a GPU loop at normal priority).
rife425: RIFE 4.25 lite against 4.22 lite (the one SmoovPlay ships), same engine, same clip: speed (running
  behind, extra frames shown, AI frame time) and quality (dB against the real frames test mode leaves out; 60 and
  120 fps output score, 90 doesn't).

The fake PS5 plays a 60 fps clip as a native 60 fps game (--native), and test mode turns it into a 30 fps game,
so every generated frame has a real one to be scored against. Settings: defaults (--settings none), nothing saved.
Reports: out\\ab\\<check>\\<session>.txt, then a table. RIFE numbers only: NVIDIA VFG's can't be published.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CLIP = ROOT / "testdata" / "skate_1080p60.mp4"
FIELDS = {  # report line -> what to pull out
    "behind": r"Running behind: ([\d.]+)%",
    "extra shown": r"Extra frames for 90/120 fps: (\d+)% shown",
    "AI ms": r"AI frame time: ([\d.]+) ms on average",
    "AI p95 ms": r"([\d.]+) ms for the slowest 5%",
    "delay ms": r"reached the screen (\d+) ms after it arrived",
    "AI dB": r"AI frames scored ([\d.]+) dB",
    "repeat dB": r"instead \(no AI\) scores ([\d.]+) dB",
}


def session(name: str, out: Path, seconds: float, hz: int, extra: list[str], env: dict | None = None) -> dict:
    tap = "Local\\sp-ab"
    report = out / f"{name}.txt"
    report.unlink(missing_ok=True)
    fake = subprocess.Popen([PY, str(ROOT / "scripts" / "frametap_fake.py"), str(CLIP), "--native", "--seconds",
                             str(seconds + 8), "--delay", "8", "--tap", tap], cwd=ROOT)
    try:
        subprocess.run([PY, "run.py", "--chiaki", "--chiaki-tap", tap, "--display", "--test-mode", "--hz", str(hz),
                        "--settings", "none", "--no-minimize-chiaki", "--results", str(report), *extra],
                       cwd=ROOT, env={**os.environ, **(env or {})}, timeout=seconds + 240)
    finally:
        fake.kill()
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    row = {k: (m.group(1) if (m := re.search(rx, text)) else "-") for k, rx in FIELDS.items()}
    print(f"[ab] {name}: {row}")
    return row


def table(rows: dict[str, dict]) -> str:
    cols = list(FIELDS)
    width = max(len(n) for n in rows) + 2
    lines = ["".ljust(width) + "".join(c.rjust(12) for c in cols)]
    lines += [n.ljust(width) + "".join(r[c].rjust(12) for c in cols) for n, r in rows.items()]
    return "\n".join(lines)


def load(seconds: float) -> None:
    """Background load for the mmcss check: every CPU core but two spinning, plus a GPU matrix loop that takes
    about half the card, all at normal priority (as a game launcher, a browser or a recorder would)."""
    import multiprocessing as mp
    n = max(1, (os.cpu_count() or 4) - 2)
    procs = [mp.Process(target=_spin, args=(seconds,), daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    import torch
    a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        for _ in range(8):
            a = (a @ a).clamp_(-1, 1)
        torch.cuda.synchronize()
        time.sleep(0.004)
    for p in procs:
        p.join()


def _spin(seconds: float) -> None:
    t_end = time.perf_counter() + seconds
    x = 0
    while time.perf_counter() < t_end:
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", choices=["mmcss", "rife425", "load"])
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--hz", type=int, default=None, help="output rate (mmcss: 90, rife425: 60)")
    ap.add_argument("--engine", default="torch", choices=["torch", "trt"],
                    help="rife425: the engine for both (TensorRT builds an engine for 4.25 lite the first time)")
    a = ap.parse_args()
    if a.check == "load":
        load(a.seconds)
        return 0
    out = ROOT / "out" / "ab" / a.check
    out.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    if a.check == "mmcss":
        hz = a.hz or 90
        for loaded in (False, True):
            bg = (subprocess.Popen([PY, __file__, "load", "--seconds", str(2 * a.seconds + 120)], cwd=ROOT)
                  if loaded else None)
            try:
                for boost in (True, False):
                    name = f"{'busy' if loaded else 'idle'}, boost {'on' if boost else 'off'}"
                    rows[name] = session(name.replace(", ", "-").replace(" ", "-"), out, a.seconds, hz, [],
                                         None if boost else {"FRAMEGEN_NO_BOOST": "1"})
            finally:
                if bg is not None:
                    bg.kill()
    else:
        hz = a.hz or 60
        for model in ("4.22.lite", "4.25.lite"):
            rows[f"RIFE {model}"] = session(f"rife-{model}", out, a.seconds, hz,
                                            ["--model", model, "--engine", a.engine])
    text = table(rows)
    print(text)
    (out / "table.txt").write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Summarises scripts/presentmon_check.ps1's PresentMon CSVs: how SmoovPlay's frames reached the screen.

    .venv\\Scripts\\python scripts\\presentmon_summary.py out\\presentmon

Per session (windowed, fullscreen, windowed-vsync, and dxgi-* for Window presentation: Low delay):
  - present mode: "Hardware: Independent Flip" / "Hardware Composed: Independent Flip" go straight to the screen;
    "Composed: Flip" / "Composed: Copy..." go through the Windows compositor, which adds about a refresh.
  - until displayed: from our Present call to the frame reaching the screen (median / 95th percentile).
  - dropped: presented frames that were never shown (replaced before the screen got to them).
  - between display changes: how evenly frames reached the screen.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import numpy as np

UNTIL_DISPLAYED = ("MsUntilDisplayed", "DisplayLatency", "msUntilDisplayed")
BETWEEN_DISPLAY = ("MsBetweenDisplayChange", "msBetweenDisplayChange")
BETWEEN_PRESENTS = ("MsBetweenPresents", "msBetweenPresents")


def col(row: dict, names: tuple) -> float | None:
    for n in names:
        if n in row:
            try:
                return float(row[n])
            except (TypeError, ValueError):
                return None
    return None


def summarise(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:  # PresentMon 2.x writes a BOM
        rows = [r for r in csv.DictReader(f) if r.get("Application", "").lower() == "python.exe"]
    if not rows:
        return [f"{path.stem}: no frames from python.exe"]
    # The busiest swap chain is the video window (the fake PS5 and helpers present nothing).
    chains = Counter(r.get("SwapChainAddress", "") for r in rows)
    chain = chains.most_common(1)[0][0]
    rows = [r for r in rows if r.get("SwapChainAddress", "") == chain]
    modes = Counter(r.get("PresentMode", "?") for r in rows)
    until = [v for r in rows if (v := col(r, UNTIL_DISPLAYED)) is not None]
    between = [v for r in rows if (v := col(r, BETWEEN_DISPLAY)) is not None and v > 0]
    presents = [v for r in rows if (v := col(r, BETWEEN_PRESENTS)) is not None and v > 0]
    dropped = len(rows) - len(until)
    pct = lambda xs, p: float(np.percentile(xs, p)) if xs else float("nan")  # noqa: E731
    lines = [f"{path.stem}: {len(rows)} presents, runtime {rows[0].get('PresentRuntime', '?')}, "
             f"tearing allowed {rows[0].get('AllowsTearing', '?')}, sync interval {rows[0].get('SyncInterval', '?')}"]
    lines.append("  present mode: " + ", ".join(f"{m} {100 * n / len(rows):.0f}%" for m, n in modes.most_common()))
    lines.append(f"  until displayed: median {pct(until, 50):.1f} ms, p95 {pct(until, 95):.1f} ms")
    lines.append(f"  dropped (never shown): {dropped} ({100 * dropped / len(rows):.1f}%)")
    if presents:
        lines.append(f"  presents: every {pct(presents, 50):.2f} ms (median) = {1000 / pct(presents, 50):.0f} per second")
    if between:
        lines.append(f"  between display changes: median {pct(between, 50):.2f} ms, p5 {pct(between, 5):.2f}, "
                     f"p95 {pct(between, 95):.2f}")
    return lines


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out/presentmon")
    csvs = [p for p in sorted(folder.glob("*.csv")) if not p.stem.endswith("-ticks")]
    if not csvs:
        print(f"no PresentMon CSVs in {folder}")
        return 1
    for p in csvs:
        print("\n".join(summarise(p)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

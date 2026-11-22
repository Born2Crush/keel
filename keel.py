"""Keel: the play window for a PS5 on this PC.

The same picture pipeline as the rest of this tree (frame generation, upscaling, Looks, instant
replay, the play window and its menu). What Keel adds is the start: it owns the Remote Play
session, so a stream that began before Keel is restarted instead of leaving the window on
"No picture from the PS5".

    python keel.py
    python keel.py --hz 120 --output-size 2160p

chiaki-ng must be the build with the frame tap (chiaki_tap/). If it isn't already running, set
KEEL_CHIAKI to chiaki.exe and Keel starts it with CHIAKI_FRAMETAP=1. The first time, register the
PS5 in chiaki-ng as usual. Free to use. No account.
"""

from __future__ import annotations

import os
import sys


def _with_chiaki(argv: list[str]) -> list[str]:
    """No source picked: PS5 Remote Play, on screen."""
    if any(a == "--chiaki" or a == "--capture" or a.startswith("--capture=") or a == "--source"
           or a.startswith("--source=") or a in ("--list-sources", "--list-devices") for a in argv):
        return argv
    # A bare path is a video file. The value after an option (2160p, 120) is not.
    for i, a in enumerate(argv):
        if a.startswith("-"):
            continue
        prev = argv[i - 1] if i else ""
        if not prev.startswith("-"):
            return argv
    return ["--chiaki", "--display", *argv]


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("FRAMEGEN_TITLE", "Keel")
    os.environ["KEEL_OWN_SESSION"] = "1"
    args = list(sys.argv[1:] if argv is None else argv)
    sys.argv = [sys.argv[0], *_with_chiaki(args)]
    from run import main as run_main
    return run_main()


if __name__ == "__main__":
    sys.exit(main())

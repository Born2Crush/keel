"""User settings: one registry (SPECS) that the in-video menu, the launcher's Settings page, the command line and
the saved file all share.

To add a setting: add a Spec here, then read `settings.<key>` where it matters (or `settings.watch(fn)` to hear
about changes). `live=False` settings are saved at once but take effect at the next in-app restart; the menu
offers "Restart now" when any are pending.

`python -m framegen.settings` prints the registry as JSON for the launcher (specs_json()), so it never keeps a copy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import fill, looks

DEFAULT_PATH = Path(os.environ.get("APPDATA", Path.home())) / "framegen" / "settings.json"


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    choices: tuple
    default: Any
    names: dict = field(default_factory=dict)
    live: bool = True
    help: str = ""
    saved: bool = True
    group: str = ""
    quick: bool = False
    applies: tuple = ()
    later: str = ""
    hints: dict = field(default_factory=dict)
    impact: dict = field(default_factory=dict)
    experimental: bool = False
    sources: tuple = ()
    elsewhere: dict = field(default_factory=dict)

    def name(self, value: Any) -> str:
        return self.names.get(value, str(value))

    def applies_to(self, values: dict) -> bool:
        return not self.applies or values.get(self.applies[0]) in self.applies[1]

    def unused_with(self, source: str) -> str | None:
        if not self.sources or source in self.sources:
            return None
        return self.elsewhere.get(source) or self.elsewhere.get("") or "Not used with this source."


ON_OFF = {True: "On", False: "Off"}
CAPTURE_SIZES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "2160p": (3840, 2160)}
IMPACT_PC = "an RTX 4070 Ti SUPER"
LOOK_SPEED = "About 0.02 to 0.35 ms a frame of graphics card time for Keel's looks (1440p)."
LOOK_CLIPS_SPEED = "Up to about 0.2 ms more a frame of graphics card time, to draw it again at 1080p."

SPECS_A: tuple[Spec, ...] = (
    Spec("interp", "Frame generation", (True, False), True, ON_OFF, group="Smoothness", quick=True,
         hints={False: "No new frames: the stream exactly as the game sends it, with the least delay."},
         impact={"picture": "Smoother motion. Small, fast details can wobble a little.",
                 "speed": "Adds delay: about 20 ms at 60 fps, about 40 ms at 90 or 120 fps."},
         help="Draws new frames between the ones the game sends, so a 30 fps game moves like 60, 90 or 120 fps. "
              "Off shows the stream exactly as it arrives. In the video window, I turns it off and on for this "
              "session only, to compare."),
    Spec("hz", "Output frame rate", (60, 90, 120), 120, {60: "60 fps", 90: "90 fps", 120: "120 fps"},
         group="Smoothness", quick=True,
         hints={60: "One new frame for each real one. Adds about 20\u00a0ms of delay.",
                90: "Two new frames for each real one. Needs a 90\u00a0Hz or faster display. Adds about 40\u00a0ms of delay.",
                120: "What Keel starts with. Three new frames for each real one. Needs a 120\u00a0Hz display. Adds about 40\u00a0ms of delay."},
         help="How many frames you see each second. Keel starts at 120. That needs a display that runs at least "
              "that fast (G-Sync or FreeSync helps). 90 and 60 are there when the display is slower. Auto-detect, "
              "on Keel's Settings page, can still suggest a lower rate from its own timing."),
    Spec("scaler", "Upscaler", ("vsr", "fsr", "bilinear"), "vsr",
         {"vsr": "NVIDIA RTX VSR", "fsr": "AMD FSR 1", "bilinear": "Bilinear"},
         group="Picture", quick=True,
         later="Support for other upscalers may come in a later version.",
         hints={"vsr": "What Keel starts with. NVIDIA RTX Video Super Resolution. It comes with NVIDIA VFG's download (RTX 40 and 50 series).",
                "fsr": "The fallback when RTX VSR isn't available. Sharp, and about 0.1\u00a0ms a frame.",
                "bilinear": "Plain resizing, the softest. For comparison."},
         help="RTX Video Super Resolution is the upscaler. A 1080p stream, usually PS5 Remote Play, is drawn at "
              "3840x2160 when Output size is 4K, then fitted to the window. It comes with NVIDIA VFG's download "
              "from NVIDIA, on RTX 40 and 50 series cards. AMD FSR 1 is the fallback. Key: U.",
         impact={"picture": "Only matters on a display bigger than the stream, like 1440p or 4K.",
                 "speed": "RTX VSR runs an AI model on every frame. AMD FSR 1 is about 0.1 ms a frame."}),
    Spec("output_size", "Output size", ("window", "2160p"), "2160p",
         {"window": "Window", "2160p": "4K"}, group="Picture", experimental=True,
         hints={"window": "Drawn at the window's size, with the upscaler above.",
                "2160p": "What Keel starts with. RTX Video Super Resolution draws 1080p at 3840x2160, then fits the window."},
         help="For a 1080p stream, usually PS5 Remote Play. Keel starts at 4K: RTX Video Super Resolution, "
              "then fitted to the window. Frame generation stays at 1080p. Needs an NVIDIA RTX 40 or 50 series "
              "card and NVIDIA VFG's download, which brings RTX VSR. Experimental.",
         impact={"picture": "Sharper on a 4K screen. No extra detail on a 1080p screen.",
                 "speed": "RTX VSR runs on the GPU for every frame. Frame generation stays at the stream size."}),
)

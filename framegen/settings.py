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
    names: dict = field(default_factory=dict)  # value -> text shown; missing values are shown as str(value)
    live: bool = True  # applies immediately; False = on the next restart
    help: str = ""
    saved: bool = True  # False: this session only, never written to the settings file (e.g. test modes)
    group: str = ""  # section on the launcher's Settings page; SPECS lists each section's rows together
    quick: bool = False  # one of the few big choices at the top of the launcher's Settings page
    applies: tuple = ()  # (key, values): only used, and only shown, while setting `key` is one of `values`
    later: str = ""  # a note at the end of the launcher's dropdown, about choices that aren't offered yet
    hints: dict = field(default_factory=dict)  # value -> a line under that choice in the launcher's dropdown (only
    # there, so no-break spaces keep "90 Hz" together; the in-video menu's font can't draw them, so help has none)
    impact: dict = field(default_factory=dict)  # "picture" and/or "speed": a rough line on what it does to the picture
    # and to speed or delay, shown in the launcher and the in-video menu (plain ASCII). Numbers are measured (IMPACT_PC).
    experimental: bool = False  # built without the hardware (or a real game) to test it: tagged as Experimental in the
    # launcher (with Send feedback) and in the in-video menu
    sources: tuple = ()  # only used with these sources (framegen/sources.py kinds; () = all). With another one, the
    # launcher (for the source picked on its Play page) and the in-video menu (run.py) grey it out, with its note:
    elsewhere: dict = field(default_factory=dict)  # source kind -> why it isn't used there ("": any other source)

    def name(self, value: Any) -> str:
        return self.names.get(value, str(value))

    def applies_to(self, values: dict) -> bool:
        """Whether this setting is used with these values (e.g. VFG quality only while NVIDIA VFG is picked)."""
        return not self.applies or values.get(self.applies[0]) in self.applies[1]

    def unused_with(self, source: str) -> str | None:
        """Why this setting isn't used with frames from `source` (a framegen/sources.py kind), or None if it is."""
        if not self.sources or source in self.sources:
            return None
        return self.elsewhere.get(source) or self.elsewhere.get("") or "Not used with this source."


ON_OFF = {True: "On", False: "Off"}
CAPTURE_SIZES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "2160p": (3840, 2160)}  # Capture card resolution
IMPACT_PC = "an RTX 4070 Ti SUPER"  # where the numbers in `impact` and the hints were measured
# scripts/looks_timing.py: GPU time of the built-in looks on a 1440p window (0.02 to 0.34 ms) and at 1080p for clips
# (0.01 to 0.19 ms), measured while chiaki-ng streamed.
LOOK_SPEED = "About 0.02 to 0.35 ms a frame of graphics card time for Keel's looks (1440p)."
LOOK_CLIPS_SPEED = "Up to about 0.2 ms more a frame of graphics card time, to draw it again at 1080p."

SPECS: tuple[Spec, ...] = (
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
                90: "Two new frames for each real one. Needs a 90\u00a0Hz or faster display. Adds about 40\u00a0ms of "
                    "delay.",
                120: "What Keel starts with. Three new frames for each real one. Needs a 120\u00a0Hz display. Adds about "
                     "40\u00a0ms of delay."},
         help="How many frames you see each second. Keel starts at 120. That needs a display that runs at least "
              "that fast (G-Sync or FreeSync helps). 90 and 60 are there when the display is slower. Auto-detect, "
              "on Keel's Settings page, can still suggest a lower rate from its own timing."),
    Spec("scaler", "Upscaler", ("dlss", "fsr", "vsr", "bilinear"), "dlss",
         {"dlss": "NVIDIA DLSS", "fsr": "AMD FSR 1", "vsr": "NVIDIA RTX VSR", "bilinear": "Bilinear"},
         group="Picture", quick=True,
         later="Support for other upscalers may come in a later version.",
         hints={"dlss": "What Keel starts with. NVIDIA DLSS, Performance (2x): a 1080p stream is drawn at 4K.",
                "fsr": "Sharp and almost free: about 0.1\u00a0ms a frame.",
                "vsr": "NVIDIA's AI upscaler, Experimental. It comes with NVIDIA VFG's download (RTX 40 and 50 "
                       "series); without it, DLSS is used. Heavier.",
                "bilinear": "Plain resizing, the softest. For comparison."},
         help="NVIDIA DLSS is the 4K upscaler. A 1080p stream, usually PS5 Remote Play, is drawn at 3840x2160 "
              "and then fitted to the window. It uses the DLSS library that comes with the NVIDIA driver. "
              "AMD FSR 1 is the light fallback. Key: U.",
         impact={"picture": "Only matters on a display bigger than the stream, like 1440p or 4K.",
                 "speed": "About 0.1 ms a frame for FSR 1. RTX VSR runs an AI model on every frame, which can "
                          "cost in-between frames at 90 and 120 fps."}),
    Spec("output_size", "Output size", ("window", "2160p"), "2160p",
         {"window": "Window", "2160p": "4K"}, group="Picture", experimental=True,
         hints={"window": "Drawn at the window's size, with the upscaler above.",
                "2160p": "What Keel starts with. NVIDIA DLSS, Performance (2x), 1080p to 3840x2160."},
         help="For a 1080p stream, usually PS5 Remote Play. Keel starts at 4K: NVIDIA DLSS Super Resolution, "
              "Performance mode, then fitted to the window. Frame generation stays at 1080p. Needs an NVIDIA RTX "
              "card and NVIDIA's DLSS library, which comes with the driver. Experimental.",
         impact={"picture": "Sharper on a 4K screen. No extra detail on a 1080p screen.",
                 "speed": "About 2.1 ms a frame on an RTX 4080. An RTX 3060 can take about 11 ms, which can drop "
                          "frames at 90 and 120 fps."}),
)
BY_KEY = {s.key: s for s in SPECS}


def _bring_dlss(values: dict, path: Path) -> dict:
    """Older builds saved AMD FSR 1 because NVIDIA DLSS was not a choice. Start those on DLSS once."""
    if values.get("scaler") != "fsr":
        return values
    marker = path.with_name(".keel-dlss")
    if marker.is_file():
        return values
    values = dict(values)
    values["scaler"] = "dlss"
    if values.get("output_size", "window") == "window":
        values["output_size"] = "2160p"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, indent=2), encoding="utf-8")
        marker.write_text("1", encoding="utf-8")
    except OSError:
        pass
    return values


class Settings:
    def __init__(self, values: dict | None = None, path: Path | None = None) -> None:
        self.path = path
        self._listeners: list[Callable[[str], None]] = []
        self._v = {s.key: s.default for s in SPECS}
        self._saved = dict(self._v)
        for k, v in (values or {}).items():
            if k in BY_KEY and BY_KEY[k].saved and v in BY_KEY[k].choices:
                self._v[k] = self._saved[k] = v

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Settings":
        try:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            values = {}
        if not isinstance(values, dict):
            values = {}
        return cls(_bring_dlss(values, Path(path)), path)

    def __getattr__(self, key: str) -> Any:
        try:
            return self.__dict__["_v"][key]
        except KeyError:
            raise AttributeError(key) from None

    def values(self) -> dict:
        return dict(self._v)

    def watch(self, fn):
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def override(self, key: str, value) -> None:
        self._put(key, value, save=False)

    def set(self, key: str, value) -> None:
        self._put(key, value, save=True)

    def _put(self, key: str, value, save: bool) -> None:
        if value not in BY_KEY[key].choices:
            raise ValueError(f"{key}: {value!r} is not one of {BY_KEY[key].choices}")
        changed = self._v[key] != value
        self._v[key] = value
        if save and BY_KEY[key].saved:
            self._saved[key] = value
            self.save()
        if changed:
            for fn in list(self._listeners):
                fn(key)

    def step(self, key: str, delta: int, allowed=None):
        choices = BY_KEY[key].choices
        i = choices.index(self._v[key])
        for _ in range(len(choices)):
            i = (i + delta) % len(choices)
            if allowed is None or allowed(key, choices[i]):
                self.set(key, choices[i])
                break
        return self._v[key]

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._saved, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[settings] couldn't save {self.path}: {e}")


def specs_json(specs: tuple[Spec, ...] = SPECS) -> dict:
    return {
        "path": str(DEFAULT_PATH),
        "impact_pc": IMPACT_PC,
        "specs": [{"key": s.key, "label": s.label, "group": s.group, "help": s.help, "default": s.default,
                   "live": s.live, "saved": s.saved, "later": s.later, "quick": s.quick, "impact": s.impact,
                   "experimental": s.experimental,
                   "sources": list(s.sources) or None,
                   "elsewhere": s.elsewhere or None,
                   "applies": {"key": s.applies[0], "values": list(s.applies[1])} if s.applies else None,
                   "choices": [{"value": v, "name": s.name(v), "hint": s.hints.get(v, "")} for v in s.choices]}
                  for s in specs],
    }


if __name__ == "__main__":
    print(json.dumps(specs_json()))

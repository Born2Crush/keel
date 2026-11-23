"""Make the Windows build show and draw NVIDIA DLSS.

The Play page chip used to label every upscaler except VSR and bilinear as AMD FSR 1, so picking
DLSS still read AMD FSR 1. The play window also left DLSS out of the scalers it will draw, so the
choice was refused and the picture stayed FSR. Run this before installer/build.py. Safe to run twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _need(text: str, old: str, new: str, label: str) -> str:
    if old == new or new in text:
        return text
    if old not in text:
        raise SystemExit(f"DLSS patch could not find {label}")
    return text.replace(old, new, 1)


def patch_ui(text: str) -> str:
    text = text.replace('SettingText("scaler") ?? "fsr"', 'SettingText("scaler") ?? "dlss"')
    old = 'scaler == "vsr" ? "NVIDIA RTX VSR" : scaler == "bilinear" ? "Bilinear" : "AMD FSR 1"'
    new = 'scaler == "dlss" ? "NVIDIA DLSS" : ' + old
    return _need(text, old, new, "the Play page upscaler chip")


def patch_io(text: str) -> str:
    text = _need(
        text,
        'NAMES = {"fsr": "FSR 1", "vsr": "RTX VSR", "bilinear": "bilinear"}',
        'NAMES = {"dlss": "DLSS", "fsr": "FSR 1", "vsr": "RTX VSR", "bilinear": "bilinear"}',
        "the upscaler names",
    )
    text = _need(
        text,
        'self.modes = [m for m, ok in (("fsr", self.fsr), ("vsr", self._vsr_args), ("bilinear", True)) if ok]',
        'self.modes = [m for m, ok in (("dlss", True), ("fsr", self.fsr), ("vsr", self._vsr_args), ("bilinear", True)) if ok]',
        "the upscaler list",
    )
    text = _need(
        text,
        'details = {"fsr": f" (sharpness {sharpness:g})", "vsr": f" (quality {vsr_quality})", "bilinear": ""}',
        'details = {"dlss": "", "fsr": f" (sharpness {sharpness:g})", "vsr": f" (quality {vsr_quality})", "bilinear": ""}',
        "the upscaler detail line",
    )
    text = _need(
        text,
        "tw, th, offscreen = picture_target(self.settings.output_size, self.w, self.h, dw, dh)",
        "from .dlss import uses_dlss as _uses_dlss\n"
        "        _want_dlss = _uses_dlss(self.settings.output_size, self.w, self.h, getattr(self.settings, \"scaler\", None))\n"
        "        tw, th, offscreen = picture_target(\"2160p\" if _want_dlss else self.settings.output_size, self.w, self.h, dw, dh)",
        "the 4K picture target",
    )
    text = _need(
        text,
        'mode = "dlss" if uses_dlss(self.settings.output_size, self.w, self.h) and upscale else self.mode',
        'mode = "dlss" if _want_dlss and upscale else self.mode',
        "the DLSS draw",
    )
    text = _need(
        text,
        "if self.overlay and isinstance(text, MiniText):\n            self._draw_stats(text, ux, uy, uw, uh)",
        "if self.overlay and isinstance(text, MiniText):\n"
        "            self._draw_stats(_with_scaler(text, label), ux, uy, uw, uh)",
        "the status line",
    )
    text = _need(
        text,
        "if self._draw_dlss(frame, on_gpu, dest, pw, ph):\n"
        "                if plan is not None:",
        "if self._draw_dlss(frame, on_gpu, dest, pw, ph):\n"
        "                if getattr(self, \"_dlss_kind\", \"\") == \"image\":\n"
        "                    label = \"NVIDIA SR\"\n"
        "                if plan is not None:",
        "the image fallback label",
    )
    text = _need(
        text,
        "out = scaler.upscale(frame if on_gpu else _host(frame))\n"
        "                    self._upload_rgb(tex, pw, ph, out)",
        "out = scaler.upscale(frame if on_gpu else _host(frame))\n"
        "                    self._dlss_kind = getattr(scaler, \"kind\", \"dlss\")\n"
        "                    self._upload_rgb(tex, pw, ph, out)",
        "the upscaler kind",
    )
    text = _need(
        text,
        'mode, label = "bilinear", "DLSS unavailable"',
        'why = str(getattr(self, "_dlss_why", "") or "unavailable").replace("DLSS ", "")\n'
        '                mode, label = "bilinear", "DLSS " + why[:42]',
        "the DLSS failure line",
    )
    text = _need(
        text,
        'if not getattr(self, "_said_dlss_why", False):',
        'self._dlss_why = why or "unavailable"\n        if not getattr(self, "_said_dlss_why", False):',
        "the DLSS failure note",
    )
    if "def _with_scaler(" not in text:
        text = text.replace(
            "    @classmethod\n    def make(cls, parts, health: str = \"\", second: str = \"\") -> \"MiniText\":\n"
            "        t = cls(\"  \".join(x for x, _ in parts) + (f\"\\n{second}\" if second else \"\"))\n"
            "        t.parts, t.health, t.second = tuple(parts), health, second\n"
            "        return t\n",
            "    @classmethod\n    def make(cls, parts, health: str = \"\", second: str = \"\") -> \"MiniText\":\n"
            "        t = cls(\"  \".join(x for x, _ in parts) + (f\"\\n{second}\" if second else \"\"))\n"
            "        t.parts, t.health, t.second = tuple(parts), health, second\n"
            "        return t\n\n\n"
            "def _with_scaler(text: MiniText, label: str) -> MiniText:\n"
            "    \"\"\"The default status line names the upscaler.\"\"\"\n"
            "    if not label or label == \"no upscale\":\n"
            "        return text\n"
            "    return MiniText.make(list(text.parts) + [(label, \"dim\")], text.health, text.second)\n",
            1,
        )
        if "def _with_scaler(" not in text:
            raise SystemExit("DLSS patch could not find the status-line helper")
    return text


def main() -> None:
    ui = ROOT / "installer" / "launcher" / "Ui.cs"
    io = ROOT / "framegen" / "io.py"
    ui.write_text(patch_ui(ui.read_text(encoding="utf-8")), encoding="utf-8")
    io.write_text(patch_io(io.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS chip and draw path are in the build")


if __name__ == "__main__":
    main()

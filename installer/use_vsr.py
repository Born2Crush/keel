"""Build-time switch from DLSS to RTX Video Super Resolution. Safe to run twice."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_io(text: str) -> str:
    if "def _draw_dlss(" not in text:
        return text
    text = text.replace(
        "self._uhd = None  # 4K output: DLSS draws 3840x2160 here, then it is fitted to the window\n"
        "        self._dlss_gl = None  # CUDA copy into that image\n",
        "self._uhd = None  # 4K output: the upscaler draws 3840x2160 here, then it is fitted to the window\n",
        1,
    )
    text = text.replace(
        'print(f"[display] 4K output: NVIDIA DLSS, {self.w}x{self.h} to {tw}x{th}, then fitted to the window")',
        'print(f"[display] 4K output: RTX Video Super Resolution, {self.w}x{self.h} to {tw}x{th}, then fitted to the window")',
        1,
    )
    old = '''        upscale = pw > self.w or ph > self.h
        from .dlss import uses_dlss
        mode = "dlss" if uses_dlss(self.settings.output_size, self.w, self.h) and upscale else self.mode
        if mode == "vsr" and not on_gpu:  # a CPU frame: VSR needs it on the GPU
            mode = "fsr" if self.fsr is not None else "bilinear"
        label = "DLSS" if mode == "dlss" else (self._name(mode) if upscale else "no upscale")
        self._frame_no += 1
        plan = self.look_gl.plan(self.looks.current(), self.looks.rev) if self.look_gl is not None else None
        q = self.timer.begin()
        drew = False
        if mode == "dlss":
            # The look reads its own input texture and draws into the 4K image, so DLSS must not
            # write the texture the look is also reading.
            dest = self.look_gl.input_target(pw, ph)[0] if plan is not None else self._uhd[0]
            if self._draw_dlss(frame, on_gpu, dest, pw, ph):
                if plan is not None:
                    self.look_gl.draw(plan, dest, True, target, px, py, pw, ph, True, self._frame_no)
                    GL.glViewport(0, 0, fw, fh)
                    label += " + look"
                drew = True
            else:
                mode, label = "bilinear", "DLSS unavailable"  # the stream still shows; FSR is not labeled DLSS
        if drew:
            pass
        elif plan is not None:'''
    new = '''        upscale = pw > self.w or ph > self.h
        mode = self.mode
        if mode == "vsr" and not on_gpu:  # a CPU frame: VSR needs it on the GPU
            mode = "fsr" if self.fsr is not None else "bilinear"
        label = self._name(mode) if upscale else "no upscale"
        self._frame_no += 1
        plan = self.look_gl.plan(self.looks.current(), self.looks.rev) if self.look_gl is not None else None
        q = self.timer.begin()
        if plan is not None:'''
    if old not in text:
        raise SystemExit("VSR patch could not find the DLSS draw")
    text = text.replace(old, new, 1)
    start = text.find("    def _draw_dlss(")
    end = text.find("    def _uhd_fbo(")
    if start < 0 or end < 0:
        raise SystemExit("VSR patch could not remove the DLSS helpers")
    text = text[:start] + text[end:]
    if "def _draw_dlss(" in text or "uses_dlss" in text:
        raise SystemExit("VSR patch left a DLSS call in the window")
    return text


def patch_auto(text: str) -> str:
    if 'current.get("scaler") == "dlss"' in text:
        return text
    old = '''    """AMD FSR 1, unless RTX VSR is picked and still fits in what frame generation leaves (`left` ms)."""
    def vsr_ms(q: int) -> float:
        return VSR_MS[q] * speed * pixels / REF_PIXELS

    size = f"{display.width}×{display.height}" if display else "your display"
    if current.get("scaler") == "vsr" and vsr is None and enlarge:'''
    new = '''    """RTX VSR when it is picked and still fits. A saved DLSS choice is RTX VSR. AMD FSR 1 otherwise."""
    def vsr_ms(q: int) -> float:
        return VSR_MS[q] * speed * pixels / REF_PIXELS

    size = f"{display.width}×{display.height}" if display else "your display"
    picked = "vsr" if current.get("scaler") == "dlss" else current.get("scaler")
    if picked == "vsr" and vsr is None and enlarge:'''
    if old not in text:
        raise SystemExit("VSR patch could not find auto-detect")
    text = text.replace(old, new, 1)
    text = text.replace('if current.get("scaler") == "vsr":', 'if picked == "vsr":', 1)
    return text


def patch_ui(text: str) -> str:
    text = text.replace('SettingText("scaler") ?? "fsr"', 'SettingText("scaler") ?? "vsr"')
    text = text.replace('SettingText("scaler") ?? "dlss"', 'SettingText("scaler") ?? "vsr"')
    text = text.replace(
        'scaler == "dlss" ? "NVIDIA DLSS" : scaler == "vsr" ? "NVIDIA RTX VSR"',
        'scaler == "vsr" || scaler == "dlss" ? "NVIDIA RTX VSR"',
    )
    text = text.replace(
        'scaler == "vsr" ? "NVIDIA RTX VSR" : scaler == "bilinear" ? "Bilinear" : "AMD FSR 1"',
        'scaler == "vsr" || scaler == "dlss" ? "NVIDIA RTX VSR" : scaler == "bilinear" ? "Bilinear" : "AMD FSR 1"',
    )
    if 'scaler == "vsr" || scaler == "dlss"' not in text:
        raise SystemExit("VSR patch could not name the Play chip")
    return text


def patch_run(text: str) -> str:
    text = text.replace(
        "2160p: NVIDIA DLSS Performance, 1080p to 4K, ",
        "2160p: RTX Video Super Resolution, 1080p to 4K, ",
    )
    text = text.replace(
        "then fit to the window (needs an RTX card; frame generation stays at 1080p)",
        "then fit to the window (needs an RTX 40 or 50 series card; frame generation stays at 1080p)",
    )
    return text


def main() -> None:
    io = ROOT / "framegen" / "io.py"
    io.write_text(patch_io(io.read_text(encoding="utf-8")), encoding="utf-8")
    auto = ROOT / "framegen" / "autodetect.py"
    auto.write_text(patch_auto(auto.read_text(encoding="utf-8")), encoding="utf-8")
    ui = ROOT / "installer" / "launcher" / "Ui.cs"
    ui.write_text(patch_ui(ui.read_text(encoding="utf-8")), encoding="utf-8")
    run = ROOT / "run.py"
    run.write_text(patch_run(run.read_text(encoding="utf-8")), encoding="utf-8")
    print("RTX Video Super Resolution is the upscaler")


if __name__ == "__main__":
    main()

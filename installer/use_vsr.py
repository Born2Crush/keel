"""Build-time switch from DLSS to RTX Video Super Resolution. Safe to run twice."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_io(text: str) -> str:
    if "def _draw_dlss(" not in text:
        return _motion(text)
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
    return _motion(text)


def _motion(text: str) -> str:
    """In-between frames are scaled spatially. VSR on a blend is the smear while the camera moves."""
    if "def _spatial_scale(" in text:
        return text
    old = '''    def _name(self, mode: str) -> str:'''
    new = '''    def set_generated(self, generated: bool) -> None:
        """The pipeline says when this present is an in-between frame rather than the game's own."""
        self._generated_frame = bool(generated)

    def _spatial_scale(self, mode: str) -> bool:
        """FSR, or an in-between frame while RTX VSR is selected.

        VSR repaints a generated blend, and that is the smear while the camera moves. The game's own
        frame still goes through VSR.
        """
        return mode == "fsr" or (mode == "vsr" and self._generated_frame and self.fsr is not None)

    def _name(self, mode: str) -> str:'''
    if old not in text:
        raise SystemExit("VSR patch could not find the upscaler name")
    text = text.replace(old, new, 1)
    text = text.replace(
        "self.mode = scaler if scaler in self.modes else self.modes[0]\n",
        "self.mode = scaler if scaler in self.modes else self.modes[0]\n"
        "        self._generated_frame = False  # an in-between frame is scaled spatially; VSR smears one\n",
        1,
    )
    if 'upscale and mode == "fsr"' not in text:
        raise SystemExit("VSR patch could not find the spatial upscale")
    text = text.replace('upscale and mode == "fsr"', "upscale and self._spatial_scale(mode)")
    old_multi = '''    def write(self, frame, text: str | None = None):
        cmd = None
        for s in self.sinks:
            cmd = s.write(frame, text) or cmd
        return cmd
'''
    new_multi = '''    def set_generated(self, generated: bool) -> None:
        for s in self.sinks:
            fn = getattr(s, "set_generated", None)
            if fn is not None:
                fn(generated)

    def write(self, frame, text: str | None = None):
        cmd = None
        for s in self.sinks:
            cmd = s.write(frame, text) or cmd
        return cmd
'''
    if old_multi not in text:
        raise SystemExit("VSR patch could not find the combined sink")
    return text.replace(old_multi, new_multi, 1)


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


def patch_vsr(text: str) -> str:
    """The live-stream models. The stored-video models soften the picture while the camera moves."""
    if "STREAMING = " in text:
        return text
    old = "CLEAN = 15  # VfxVsr: quality + CLEAN = NVIDIA's high-bitrate models (16-19), for sources without compression\n"
    new = old + (
        "# Live video (Remote Play). NVIDIA's stored-video models (1-4) smear detail while the camera moves.\n"
        "# STREAMING_MEDIUM is 21, STREAMING_ULTRA is 23. Low/Medium share medium; High/Ultra share ultra.\n"
        "STREAMING = {1: 21, 2: 21, 3: 23, 4: 23}\n"
    )
    if old not in text:
        raise SystemExit("VSR patch could not find the quality models")
    text = text.replace(old, new, 1)
    old = '''    def level(quality: int, clean: bool) -> int:
        """NVIDIA's QualityLevel for our 1-4 (Low to Ultra): the same numbers, or its high-bitrate models (16-19)
        for a clean source."""
        q = min(4, max(1, int(quality)))
        return q + CLEAN if clean else q
'''
    new = '''    def level(quality: int, clean: bool) -> int:
        """NVIDIA's QualityLevel for our 1-4 (Low to Ultra).

        A clean source (a capture card) uses the high-bitrate models (16-19). A live stream uses the streaming
        models, which keep edges while the camera moves. The stored-video models (1-4) soften that motion.
        """
        q = min(4, max(1, int(quality)))
        return q + CLEAN if clean else STREAMING[q]
'''
    if old not in text:
        raise SystemExit("VSR patch could not find the quality level")
    return text.replace(old, new, 1)


def patch_pipeline(text: str) -> str:
    """Tell the window which presents are in-between frames, so VSR is not run on a blend."""
    if "generated_out" in text:
        return text
    old = '''            def present(frame, text: str):
                # Frame pacer: each present is due one gap after the previous one was *due*, so sleep overshoot and
                # present time don't accumulate; when late it goes straight away and the schedule restarts from now.
                nonlocal next_due
                if hz > 60:
                    now = time.perf_counter()
                    if next_due > now:
                        time.sleep(next_due - now)
                    next_due = max(next_due, now) + gap
                cmd = sink.write(frame, text)
'''
    new = '''            def present(frame, text: str, generated: bool = False):
                # Frame pacer: each present is due one gap after the previous one was *due*, so sleep overshoot and
                # present time don't accumulate; when late it goes straight away and the schedule restarts from now.
                nonlocal next_due
                if hz > 60:
                    now = time.perf_counter()
                    if next_due > now:
                        time.sleep(next_due - now)
                    next_due = max(next_due, now) + gap
                mark = getattr(sink, "set_generated", None)
                if mark is not None:
                    mark(generated)
                cmd = sink.write(frame, text)
'''
    if old not in text:
        raise SystemExit("VSR patch could not find present")
    text = text.replace(old, new, 1)
    text = text.replace(
        "            to_score = None\n            early_cmd = None  # 90/120 Hz: the first of this tick's two frames, already shown\n",
        "            to_score = None\n            generated_out = False  # the frame `out` is an in-between, not the game's own\n            early_cmd = None  # 90/120 Hz: the first of this tick's two frames, already shown\n",
        1,
    )
    text = text.replace(
        "early_cmd = present(self._generated(it.interpolate(slot, t), gpu_out), ov_text)",
        "early_cmd = present(self._generated(it.interpolate(slot, t), gpu_out), ov_text, True)",
        1,
    )
    old = '''                    for k, phi in enumerate(phases):
                        pl = timeline.place(phi)
                        if pl.b is None:
                            frame = pl.a.frame
                        else:
                            slot = self._timed_pair(pl.a, pl.b, slot, in_slot)
                            frame = self._generated(it.interpolate(slot, pl.t), gpu_out)
                        st.uneven_waits += pl.waiting
                        if k < len(phases) - 1:
                            early_cmd = present(frame, ov_text) or early_cmd
'''
    new = '''                    for k, phi in enumerate(phases):
                        pl = timeline.place(phi)
                        gen = pl.b is not None
                        if pl.b is None:
                            frame = pl.a.frame
                        else:
                            slot = self._timed_pair(pl.a, pl.b, slot, in_slot)
                            frame = self._generated(it.interpolate(slot, pl.t), gpu_out)
                        st.uneven_waits += pl.waiting
                        if k < len(phases) - 1:
                            early_cmd = present(frame, ov_text, gen) or early_cmd
'''
    if old not in text:
        raise SystemExit("VSR patch could not find the timed presents")
    text = text.replace(old, new, 1)
    text = text.replace(
        '''                        else:
                            out, reached = frame, pl.reached
                            action = Action.INTERP if pl.b is not None else Action.CURRENT
''',
        '''                        else:
                            out, reached = frame, pl.reached
                            generated_out = gen
                            action = Action.INTERP if pl.b is not None else Action.CURRENT
''',
        1,
    )
    text = text.replace(
        '''                elif action is Action.INTERP:
                    out = self._generated(it.interpolate(slot, mid_t), gpu_out)
''',
        '''                elif action is Action.INTERP:
                    out = self._generated(it.interpolate(slot, mid_t), gpu_out)
                    generated_out = True
''',
        1,
    )
    text = text.replace("cmd = present(out, ov_text) or early_cmd", "cmd = present(out, ov_text, generated_out) or early_cmd", 1)
    if "generated_out" not in text:
        raise SystemExit("VSR patch did not mark in-between frames")
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
    vsr = ROOT / "framegen" / "rtxvsr.py"
    vsr.write_text(patch_vsr(vsr.read_text(encoding="utf-8")), encoding="utf-8")
    pipe = ROOT / "framegen" / "pipeline.py"
    pipe.write_text(patch_pipeline(pipe.read_text(encoding="utf-8")), encoding="utf-8")
    print("RTX Video Super Resolution is the upscaler")


if __name__ == "__main__":
    main()

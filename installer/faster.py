"""200 Mbps, and the frame check overlaps the draw. Safe to run twice, after use_vsr.py."""
from pathlib import Path


def patch_rate(text: str) -> str:
    """Ask chiaki-ng for 200 Mbps, on the settings key it actually reads."""
    text = text.replace("const int bitrate = 100000;", "const int bitrate = 200000;")
    text = text.replace("H.265, 100 Mbps", "H.265, 200 Mbps")
    text = text.replace("H.265 100 Mbps", "H.265 200 Mbps")
    text = text.replace("H.265 and 100 Mbps", "H.265 and 200 Mbps")
    done = ("That is the highest Remote Play size. Keel raises chiaki-ng's bitrate slider from 100 to 200 "
            "and writes 200. The console may send less.")
    for old in (
        "That is the highest Remote Play size, and the top of chiaki-ng's bitrate slider.",
        "That is the highest Remote Play size. chiaki-ng's slider stops at 100; Keel writes 200, "
        "which chiaki-ng sends on. The console may send less.",
    ):
        text = text.replace(old, done)
    # A verbatim C# string. Two backslashes here are one in the path, which is not where chiaki-ng looks.
    text = text.replace(
        '@"Software\\\\Chiaki\\\\Chiaki\\\\settings"',
        '@"Software\\Chiaki\\Chiaki\\settings"',
    )
    text = text.replace(
        '@"Software\\\\Chiaki\\\\" + app + @"\\\\settings"',
        '@"Software\\Chiaki\\" + app + @"\\settings"',
    )
    return text


def patch_cap(text: str) -> str:
    """Before chiaki-ng starts, raise the bitrate slider that is compiled into it from 100 to 200."""
    if "RaiseBitrateCap(" in text:
        return text
    old = '            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";\n            log(ApplyVideoProfile());\n'
    new = '            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";\n            log(RaiseBitrateCap(p));\n            log(ApplyVideoProfile());\n'
    if old not in text:
        raise SystemExit("speed patch could not find where chiaki-ng starts")
    text = text.replace(old, new, 1)
    method = '''        /// <summary>chiaki-ng's bitrate slider is compiled in at 100 Mbps. Raise that to 200 in chiaki.exe
        /// before it starts, so 200 is a value the program can hold. A chiaki-ng that is already open is left
        /// alone: quit it and press Play.</summary>
        public static string RaiseBitrateCap(Paths p)
        {
            try
            {
                string script = Path.Combine(p.App, "framegen", "chiaki_bitrate.py");
                if (!File.Exists(script) || string.IsNullOrEmpty(p.Python) || !File.Exists(p.Python))
                    return "[keel] chiaki-ng bitrate cap left as it is (this copy has no updater)";
                var lines = new List<string>();
                int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.chiaki_bitrate \\"" + p.Chiaki + "\\"", p.App),
                    line => lines.Add(line), null, 20000);
                string msg = lines.Count > 0 ? lines[lines.Count - 1] : "";
                if (rc != 0)
                    return "[keel] chiaki-ng bitrate cap left as it is" + (msg.Length > 0 ? " (" + msg + ")" : "");
                return msg.Length > 0 ? msg : "[keel] chiaki-ng bitrate slider now goes to 200 Mbps";
            }
            catch (Exception e)
            {
                return "[keel] chiaki-ng bitrate cap left as it is (" + e.Message + ")";
            }
        }

        public static bool Start'''
    anchor = "        public static bool Start"
    if text.count(anchor) != 1:
        raise SystemExit("speed patch could not find Chiaki.Start")
    return text.replace(anchor, method, 1)


def _overlap(text: str) -> str:
    """The new-frame check does not wait for the previous frame's draw."""
    if "def _admit(" in text:
        return text
    old = '''            if torch.is_tensor(tick.frame):  # already on the GPU (hardware decoder)
                it.load(tick.frame)
            else:
                np.copyto(self._in_np, tick.frame)
                it.load(self._in_pinned)
            diff = self.detector.update(it.in_u8)
'''
    new = '''            diff = self._admit(it, tick.frame)
'''
    if old not in text:
        raise SystemExit("VSR patch could not find the frame check")
    text = text.replace(old, new, 1)
    text = text.replace(
        "        it, sched, st = self.interp, CadenceScheduler(), Stats(private=bool(getattr(self.interp, \"private\", False)))\n",
        "        it, sched, st = self.interp, CadenceScheduler(), Stats(private=bool(getattr(self.interp, \"private\", False)))\n"
        "        # The new-frame check runs here, off the stream that draws. It waits for the previous frame to be\n"
        "        # prepared, not for that frame's upscale and present, so those overlap the check.\n"
        "        self._detect_stream = torch.cuda.Stream() if torch.cuda.is_available() else None\n"
        "        self._prepared = None\n",
        1,
    )
    text = text.replace(
        "                    it.prepare(slot)  # (in_u8 holds this tick's frame)\n",
        "                    it.prepare(slot)  # (in_u8 holds this tick's frame)\n"
        "                    self._note_prepared()\n",
        1,
    )
    text = text.replace(
        "                self._load(a.frame)\n"
        "                self.interp.prepare(slot)\n"
        "                in_slot[slot] = a.id\n",
        "                self._load(a.frame)\n"
        "                self.interp.prepare(slot)\n"
        "                self._note_prepared()\n"
        "                in_slot[slot] = a.id\n",
        1,
    )
    text = text.replace(
        "        self._load(b.frame)\n"
        "        self.interp.prepare(slot)\n"
        "        in_slot[slot] = b.id\n",
        "        self._load(b.frame)\n"
        "        self.interp.prepare(slot)\n"
        "        self._note_prepared()\n"
        "        in_slot[slot] = b.id\n",
        1,
    )
    old_load = '''    def _load(self, frame) -> None:
'''
    new_load = '''    def _admit(self, it, frame):
        """Copy `frame` into the interpolator and classify it. See run() for why the GPU path has its own stream."""
        side = self._detect_stream
        if side is not None and torch.is_tensor(frame) and frame.is_cuda:
            if self._prepared is not None:
                side.wait_event(self._prepared)
            with torch.cuda.stream(side):
                it.load(frame)
                diff = self.detector.update(it.in_u8)
            saw = torch.cuda.Event()
            saw.record(side)
            torch.cuda.current_stream().wait_event(saw)
            return diff
        if torch.is_tensor(frame):
            it.load(frame)
        else:
            np.copyto(self._in_np, frame)
            it.load(self._in_pinned)
        return self.detector.update(it.in_u8)

    def _note_prepared(self) -> None:
        """The frame is in its slot. The next check may reuse the input once this has finished."""
        if self._detect_stream is None:
            return
        self._prepared = torch.cuda.Event()
        self._prepared.record()

    def _load(self, frame) -> None:
'''
    if old_load not in text:
        raise SystemExit("VSR patch could not find the frame load")
    if "self._detect_stream = None" not in text:
        text = text.replace(
            "        self.stats: Stats | None = None  # the running session's, for reports written while it plays\n",
            "        self.stats: Stats | None = None  # the running session's, for reports written while it plays\n"
            "        self._detect_stream = None  # set in run() when CUDA is there: the new-frame check, off the draw stream\n"
            "        self._prepared = None  # event: the interpolator has finished reading the input into a slot\n",
            1,
        )
    return text.replace(old_load, new_load, 1)


def patch_sharp(text: str) -> str:
    """Fast motion repeats the upscaled picture. Treating it as a cut dropped the frame rate."""
    if "def blit_held(" in text:
        return text
    def put(old: str, new: str, why: str) -> None:
        nonlocal text
        if old not in text:
            raise SystemExit(why)
        text = text.replace(old, new, 1)

    put(
        "        cuts = CutDetector(self.cut_threshold)\n",
        "        cuts = CutDetector(self.cut_threshold)\n"
        "        # A blended frame is the smear: the menu stays sharp and the picture behind it turns to\n"
        "        # color fog. Every in-between slot redraws the game's own frame. The upscaler still runs\n"
        "        # once, on that frame.\n"
        "        sharp_motion = True\n"
        "        last_shown = None  # the real frame last drawn, which an in-between slot shows again\n",
        "speed patch could not find the cut detector",
    )
    put(
        "            generated_out = False  # the frame `out` is an in-between, not the game's own\n",
        "            generated_out = False  # the frame `out` is an in-between, not the game's own\n"
        "            same_out = False  # this tick's last present repeats the picture already upscaled\n",
        "speed patch could not find the present flags",
    )
    put(
        "                return True\n\n            if not enabled:\n",
        "                return True\n\n"
        "            def blit_held():\n"
        "                \"\"\"One present of the picture already upscaled, on the same clock as a new frame.\n"
        "\n"
        "                Nothing is generated and the upscaler does not run. The window redraws the image it\n"
        "                already has. False is not returned: a slot that reaches here is shown.\n"
        "                \"\"\"\n"
        "                nonlocal next_due\n"
        "                if hz > 60:\n"
        "                    now = time.perf_counter()\n"
        "                    if next_due > now:\n"
        "                        time.sleep(next_due - now)\n"
        "                    next_due = max(next_due, now) + gap\n"
        "                again = getattr(sink, \"repeat\", None)\n"
        "                got = again(ov_text) if again is not None else False\n"
        "                if got is False:\n"
        "                    mark = getattr(sink, \"set_generated\", None)\n"
        "                    if mark is not None:\n"
        "                        mark(False)\n"
        "                    got = sink.write(last_shown if last_shown is not None else tick.frame, ov_text)\n"
        "                t_now = time.perf_counter()\n"
        "                shown_rate.add(t_now)\n"
        "                if replay is not None:\n"
        "                    replay.frame(last_shown if last_shown is not None else tick.frame, t_now)\n"
        "                return None if got in (True, None) else got\n"
        "\n"
        "            def show_same() -> None:\n"
        "                \"\"\"An in-between slot while the camera is moving. Skipped when this tick is already late.\"\"\"\n"
        "                nonlocal early_cmd\n"
        "                if not extra_ok:\n"
        "                    st.quarter_skipped += 1\n"
        "                    return\n"
        "                early_cmd = blit_held()\n"
        "                st.quarter_shown += 1\n"
        "\n"
        "            if not enabled:\n",
        "speed patch could not find the early present",
    )
    put(
        "                # corruption isn't blended into the generated frame. `|` so the cut detector still sees every frame.\n"
        "                is_cut = is_new and (cuts.update(diff.mean_diff, diff.cut) | tick.damaged)\n",
        "                # corruption isn't blended into the generated frame. `|` so the cut detector still sees every frame.\n"
        "                # The blend stays off the screen. A cut still drops a damaged frame out of the\n"
        "                # interpolator, and the slots themselves stay so the frame rate does not fall.\n"
        "                is_cut = is_new and (cuts.update(diff.mean_diff, diff.cut) | tick.damaged)\n",
        "speed patch could not find the cut decision",
    )
    put(
        "                elif hz == 120 and action is Action.INTERP:\n"
        "                    show_early(0.25)\n"
        "                elif hz == 120 and quarter_due and action is Action.CURRENT and not is_new:\n"
        "                    show_early(0.75)\n"
        "                elif hz == 90 and action is Action.INTERP and show_early(1 / 3):\n"
        "                    mid_t = 2 / 3\n"
        "                quarter_due = action is Action.INTERP\n"
        "                if timed:\n"
        "                    pass  # shown above\n"
        "                elif action is Action.INTERP:\n",
        "                elif hz == 120 and action is Action.INTERP:\n"
        "                    if sharp_motion:\n"
        "                        show_same()\n"
        "                        same_out = True\n"
        "                    else:\n"
        "                        show_early(0.25)\n"
        "                elif hz == 120 and quarter_due and action is Action.CURRENT and not is_new:\n"
        "                    if sharp_motion:\n"
        "                        show_same()\n"
        "                    else:\n"
        "                        show_early(0.75)\n"
        "                elif hz == 90 and action is Action.INTERP:\n"
        "                    if sharp_motion:\n"
        "                        show_same()\n"
        "                        same_out = True\n"
        "                    elif show_early(1 / 3):\n"
        "                        mid_t = 2 / 3\n"
        "                quarter_due = action is Action.INTERP\n"
        "                if timed:\n"
        "                    pass  # shown above\n"
        "                elif action is Action.INTERP and sharp_motion:\n"
        "                    same_out = True\n"
        "                    out = last_shown if last_shown is not None else tick.frame\n"
        "                elif action is Action.INTERP:\n",
        "speed patch could not find the 120 Hz slots",
    )
    put(
        "                cmd = present(out, ov_text, generated_out) or early_cmd\n",
        "                if same_out:\n"
        "                    cmd = blit_held() or early_cmd\n"
        "                else:\n"
        "                    cmd = present(out, ov_text, generated_out) or early_cmd\n"
        "                    if not generated_out:\n"
        "                        last_shown = out\n",
        "speed patch could not find the present",
    )
    put(
        "                if is_new and not timed:\n"
        "                    slot = 1 - slot\n"
        "                    it.prepare(slot)  # (in_u8 holds this tick's frame)\n",
        "                if is_new and not timed and not sharp_motion:\n"
        "                    slot = 1 - slot\n"
        "                    it.prepare(slot)  # (in_u8 holds this tick's frame)\n",
        "speed patch could not find the frame prepare",
    )
    put(
        "                        pl = timeline.place(phi)\n"
        "                        gen = pl.b is not None\n"
        "                        if pl.b is None:\n"
        "                            frame = pl.a.frame\n"
        "                        else:\n"
        "                            slot = self._timed_pair(pl.a, pl.b, slot, in_slot)\n"
        "                            frame = self._generated(it.interpolate(slot, pl.t), gpu_out)\n",
        "                        pl = timeline.place(phi)\n"
        "                        # The real frame at this moment. A blend here is the same smear.\n"
        "                        gen = False\n"
        "                        frame = pl.a.frame\n",
        "speed patch could not find the timed blend",
    )
    return text


def patch_cadence(text: str) -> str:
    """Keep the float copy of each frame, so the new-frame check does not allocate one every tick."""
    if "def _float_nchw(" in text:
        return text
    old = "        x = frame_u8.permute(2, 0, 1).unsqueeze(0).float()\n"
    if old not in text:
        raise SystemExit("VSR patch could not find the frame check copy")
    text = text.replace(old, "        x = self._float_nchw(frame_u8)\n", 1)
    text = text.replace(
        "        self.held = 0\n",
        "        self.held = 0\n"
        "        self._flt: torch.Tensor | None = None  # the float copy of the frame, kept so each tick does not allocate one\n",
        1,
    )
    old = "\n\nclass RhythmLock:\n"
    new = '''

    def _float_nchw(self, frame_u8: torch.Tensor) -> torch.Tensor:
        """HxWx3 uint8 as (1, 3, H, W) float, in a buffer kept across frames.

        The same values as `frame.permute(2, 0, 1).unsqueeze(0).float()`. Reusing the buffer
        keeps the caching allocator off this check.
        """
        nchw = frame_u8.permute(2, 0, 1).unsqueeze(0)
        flt = self._flt
        if flt is None or flt.shape != nchw.shape or flt.device != nchw.device or flt.dtype != torch.float32:
            flt = torch.empty(nchw.shape, dtype=torch.float32, device=nchw.device)
            self._flt = flt
        flt.copy_(nchw)
        return flt


class RhythmLock:
'''
    if old not in text:
        raise SystemExit("VSR patch could not find the rhythm lock")
    return text.replace(old, new, 1)



def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for path, fns in (
        (root / "installer" / "launcher" / "PlayLaunch.cs", (patch_rate, patch_cap)),
        (root / "installer" / "launcher" / "Ui.cs", (patch_rate,)),
        (root / "framegen" / "pipeline.py", (_overlap, patch_sharp)),
        (root / "framegen" / "io.py", (patch_window,)),
        (root / "framegen" / "cadence.py", (patch_cadence,)),
        (root / "framegen" / "chiaki_tap.py", (patch_color,)),
        (root / "framegen" / "vfg.py", (patch_vfg,)),
        (root / "framegen" / "rtxvsr.py", (patch_pack,)),
    ):
        text = path.read_text(encoding="utf-8")
        for fn in fns:
            text = fn(text)
        path.write_text(text, encoding="utf-8")
    print("200 Mbps, and the frame check overlaps the draw")


def patch_color(text: str) -> str:
    """Reuse the NV12 conversion's float planes. The returned image is still its own tensor."""
    if "cuda_frame_to_bgr._box" in text:
        return text
    old = '''def cuda_frame_to_bgr(fr):
    """A decoded NVDEC frame (NV12, still on the GPU) -> HxWx3 uint8 BGR CUDA tensor, without leaving the GPU.

    Matches FFmpeg's CPU conversion: BT.709 when the stream says so, else BT.601; limited range unless tagged full.
    Runs on PyTorch's default stream, which is also where FFmpeg copied the frame out, so no extra sync is needed.
    """
    import torch
    import torch.nn.functional as F
    if fr.format.name != "cuda":  # the decoder fell back to software for this stream
        return torch.from_numpy(fr.to_ndarray(format="bgr24")).cuda()
    y = torch.from_dlpack(fr.planes[0].__dlpack__())   # H x W
    uv = torch.from_dlpack(fr.planes[1].__dlpack__())  # H/2 x W/2 x 2
    kr, kb = (0.2126, 0.0722) if fr.colorspace == 1 else (0.299, 0.114)
    kg = 1.0 - kr - kb
    ys, yo, cs = (1.0, 0.0, 1.0) if fr.color_range == 2 else (255 / 219, 16.0, 255 / 224)
    lum = (y.float() - yo) * ys
    c = F.interpolate(uv.permute(2, 0, 1).unsqueeze(0).float() - 128.0, size=y.shape, mode="bilinear",
                      align_corners=False)[0] * cs
    u, v = c[0], c[1]
    b = lum + 2 * (1 - kb) * u
    g = lum - (2 * kb * (1 - kb) / kg) * u - (2 * kr * (1 - kr) / kg) * v
    r = lum + 2 * (1 - kr) * v
    return torch.stack([b, g, r], -1).clamp_(0, 255).round_().to(torch.uint8)

'''
    new = '''def cuda_frame_to_bgr(fr):
    """A decoded NVDEC frame (NV12, still on the GPU) -> HxWx3 uint8 BGR CUDA tensor, without leaving the GPU.

    Matches FFmpeg's CPU conversion: BT.709 when the stream says so, else BT.601; limited range unless tagged full.
    Runs on PyTorch's default stream, which is also where FFmpeg copied the frame out, so no extra sync is needed.
    The float planes are kept and reused. The returned image is its own tensor: the next frame must not overwrite it.
    """
    import torch
    import torch.nn.functional as F
    if fr.format.name != "cuda":  # the decoder fell back to software for this stream
        return torch.from_numpy(fr.to_ndarray(format="bgr24")).cuda()
    y = torch.from_dlpack(fr.planes[0].__dlpack__())   # H x W
    uv = torch.from_dlpack(fr.planes[1].__dlpack__())  # H/2 x W/2 x 2
    kr, kb = (0.2126, 0.0722) if fr.colorspace == 1 else (0.299, 0.114)
    kg = 1.0 - kr - kb
    ys, yo, cs = (1.0, 0.0, 1.0) if fr.color_range == 2 else (255 / 219, 16.0, 255 / 224)
    box = getattr(cuda_frame_to_bgr, "_box", None)
    if box is None or box["hw"] != tuple(y.shape) or box["uv"] != tuple(uv.shape) or box["dev"] != y.device:
        box = {
            "hw": tuple(y.shape), "uv": tuple(uv.shape), "dev": y.device,
            "y": torch.empty(y.shape, dtype=torch.float32, device=y.device),
            "c": torch.empty(uv.shape, dtype=torch.float32, device=y.device),
            "ch": torch.empty(y.shape, dtype=torch.float32, device=y.device),
        }
        cuda_frame_to_bgr._box = box
    lum = box["y"]
    lum.copy_(y).sub_(yo).mul_(ys)
    chroma = box["c"]
    chroma.copy_(uv).sub_(128.0)
    up = F.interpolate(chroma.permute(2, 0, 1).unsqueeze(0), size=y.shape, mode="bilinear",
                       align_corners=False)[0].mul_(cs)
    u, v = up[0], up[1]
    ch = box["ch"]
    out = torch.empty((*y.shape, 3), dtype=torch.uint8, device=y.device)
    ch.copy_(lum).add_(u, alpha=2 * (1 - kb))
    out[..., 0].copy_(ch.clamp_(0, 255).round_())
    ch.copy_(lum).sub_(u, alpha=(2 * kb * (1 - kb) / kg)).sub_(v, alpha=(2 * kr * (1 - kr) / kg))
    out[..., 1].copy_(ch.clamp_(0, 255).round_())
    ch.copy_(lum).add_(v, alpha=2 * (1 - kr))
    out[..., 2].copy_(ch.clamp_(0, 255).round_())
    return out

'''
    if old not in text:
        raise SystemExit("speed patch could not find the colour conversion")
    return text.replace(old, new, 1)


def patch_vfg(text: str) -> str:
    """Reuse the buffers VFG packs and unpacks, and keep the real pixel where a blend would warp."""
    if "def _prefer_real(" in text:
        return text
    if "def _pack_into(" in text:
        return _prefer_motion(text)
    pairs = (
        ('''        self.steady, self.hud, self.real = 0.0, None, None
        self._slot: int | None = None  # the newest prepared slot
''', '''        self.steady, self.hud, self.real = 0.0, None, None
        self._slot: int | None = None  # the newest prepared slot
        self._pack_v = None  # int32 copy of a real frame, kept so packing does not allocate one
        self._pack_s = None  # the high bits of an 8-bit frame, kept so widening does not allocate
        self._unf = None  # float copy of a generated frame, kept so unpacking does not allocate one
'''),
        ('''            self.slots[slot].copy_(pack_rgb10a2(self.inp))
''', '''            self._pack_into(self.inp, self.slots[slot])
'''),
        ('''            self.out_u8.copy_(unpack_rgb10a2(img.view(torch.int32), hdr=self.hdr))
''', '''            if self.hdr:
                self.out_u8.copy_(unpack_rgb10a2(img.view(torch.int32), hdr=True))
            else:
                self._unpack_u8(img.view(torch.int32))
'''),
    )
    for old, new in pairs:
        if old not in text:
            raise SystemExit("speed patch could not find a VFG buffer site")
        text = text.replace(old, new, 1)
    close = "    def close(self) -> None:\n"
    methods = '''    def _pack_into(self, bgr: torch.Tensor, out: torch.Tensor) -> None:
        """pack_rgb10a2 into `out`, reusing the int32 copy of the frame."""
        v = self._pack_v
        if v is None or v.shape != bgr.shape or v.device != bgr.device:
            v = torch.empty(bgr.shape, dtype=torch.int32, device=bgr.device)
            self._pack_v = v
        if bgr.is_floating_point():
            f = bgr.float()
            if f.data_ptr() == bgr.data_ptr():
                f = f.clone()
            v.copy_(f.clamp_(0.0, 1.0).mul_(1023.0).round_())
        else:
            v.copy_(bgr)
            top = self._pack_s
            if top is None or top.shape != v.shape or top.device != v.device:
                top = torch.empty_like(v)
                self._pack_s = top
            torch.bitwise_right_shift(v, 6, out=top)
            v.bitwise_left_shift_(2)
            v.bitwise_or_(top)  # (v << 2) | (v >> 6), 255 -> 1023
        # R in bits 0-9, G 10-19, B 20-29. bgr is B, G, R.
        torch.bitwise_or(v[..., 2], v[..., 1] << 10, out=out)
        out.bitwise_or_(v[..., 0] << 20)

    def _unpack_u8(self, packed: torch.Tensor) -> None:
        """unpack_rgb10a2 into out_u8, reusing one float image."""
        f = self._unf
        if f is None or f.shape[:2] != packed.shape or f.device != packed.device:
            f = torch.empty((*packed.shape, 3), dtype=torch.float32, device=packed.device)
            self._unf = f
        f[..., 0].copy_((packed >> 20) & 1023)  # B
        f[..., 1].copy_((packed >> 10) & 1023)  # G
        f[..., 2].copy_(packed & 1023)  # R
        f.mul_(255.0 / 1023.0)
        self.out_u8.copy_(f.round_())

'''
    if close not in text:
        raise SystemExit("speed patch could not find VFG close")
    return _prefer_motion(text.replace(close, methods + close, 1))


def _prefer_motion(text: str) -> str:
    """Where the two real frames disagree, the generated frame uses the nearer real pixel."""
    if "def _prefer_real(" in text:
        return text
    old = 'NOT_SET_UP = "NVIDIA VFG isn\'t set up on this PC. Keel\'s Settings page downloads it from NVIDIA."\n'
    new = old + '''# A generated pixel is kept while the two real frames agree, and replaced by the nearer real pixel once they
# disagree. 12 is above Remote Play's noise; 28 is a change you can see. Fast motion then stays the game's own
# picture instead of a warped blend.
REAL_LOW = 12.0
REAL_HIGH = 28.0


def real_mix(diff: float, low: float = REAL_LOW, high: float = REAL_HIGH) -> float:
    """How much of the real pixel to use at a colour difference of `diff` levels (0..255).

    0 keeps the generated pixel. 1 uses the real one."""
    if high <= low:
        return 1.0 if diff >= high else 0.0
    if diff <= low:
        return 0.0
    if diff >= high:
        return 1.0
    return (diff - low) / (high - low)
'''
    if old not in text:
        raise SystemExit("speed patch could not find the VFG setup line")
    text = text.replace(old, new, 1)
    old = "        self._unf = None  # float copy of a generated frame, kept so unpacking does not allocate one\n"
    new = old + "        self._real_bgr = [None, None]  # the two real frames, for putting their own pixels back where they disagree\n        self._mix = None\n"
    if old not in text:
        raise SystemExit("speed patch could not find the VFG scratch")
    text = text.replace(old, new, 1)
    old = "        if self.steady > 0:  # Steady HUD: the real pixels where the HUD is\n"
    new = "        if self.steady > 0:  # Steady HUD: the real pixels where the HUD is\n"
    if old not in text:
        raise SystemExit("speed patch could not find the VFG blend")
    text = text.replace(old, new, 1)
    close = "    def close(self) -> None:\n"
    methods = '''    def _keep_real(self, slot: int) -> None:
        """Remember this real frame. The generated frame puts these pixels back where the two frames disagree."""
        cur = self._real_bgr[slot]
        if cur is None or cur.shape != self.inp.shape or cur.device != self.inp.device or cur.dtype != self.inp.dtype:
            cur = torch.empty_like(self.inp)
            self._real_bgr[slot] = cur
        cur.copy_(self.inp)

    def _mix_ready(self, like: torch.Tensor) -> None:
        if self._mix is not None and self._mix.shape == like.shape and self._mix.device == like.device:
            return
        self._mix = torch.empty(like.shape, dtype=torch.float32, device=like.device)
        self._da = torch.empty_like(self._mix)
        self._db = torch.empty_like(self._mix)
        self._dd = torch.empty_like(self._mix)
        self._w = torch.empty((*like.shape[:2], 1), dtype=torch.float32, device=like.device)

    def _prefer_real(self, newest: int, t: float) -> None:
        """Where the two real frames disagree, use the nearer real pixel instead of the generated one.

        A fast camera makes the generated frame swim and double. The real pixel is the one chiaki-ng shows.
        Where the frames still agree, the generated pixel stays, so slow motion does too.
        """
        frames = self._real_bgr
        if frames[0] is None or frames[1] is None:
            return
        older, newer = frames[1 - newest], frames[newest]
        self._mix_ready(older)
        a, b, diff, mix, w = self._da, self._db, self._dd, self._mix, self._w
        a.copy_(older)
        b.copy_(newer)
        diff.copy_(a).sub_(b).abs_()
        torch.amax(diff, dim=-1, keepdim=True, out=w)
        span = 255.0 if self.out_u8.dtype == torch.uint8 else 1.0
        low, high = REAL_LOW / 255.0 * span, REAL_HIGH / 255.0 * span
        w.sub_(low).div_(high - low).clamp_(0.0, 1.0)
        near = b if t >= 0.5 else a
        mix.copy_(self.out_u8)
        diff.copy_(near).sub_(mix)
        mix.addcmul_(diff, w)
        self.out_u8.copy_(mix if self.out_u8.dtype != torch.uint8 else mix.round_())

    def close(self) -> None:
'''
    if close not in text:
        raise SystemExit("speed patch could not find VFG close for motion")
    return text.replace(close, methods, 1)


def patch_pack(text: str) -> str:
    """Reuse the images that pack a real frame for RTX VSR. The bits stay the same."""
    if "pack_rgba10._v" in text:
        return text
    old = '''def pack_rgba10(rgba: torch.Tensor, out: torch.Tensor) -> torch.Tensor:
    """HxWx4 RGBA (uint8, or float16 0..1) -> `out`, HxW int32 of NVIDIA's packed RGB10A2: R in bits 0-9, G 10-19,
    B 20-29, alpha 30-31 (opaque). uint8 values are widened exactly (v10 = v8 * 1023 / 255); floats rounded."""
    if rgba.is_floating_point():
        v = rgba[..., :3].float().clamp_(0.0, 1.0).mul_(1023.0).round_().to(torch.int32)
    else:
        v = rgba[..., :3].to(torch.int32)
        v = (v << 2) | (v >> 6)
    return torch.bitwise_or(v[..., 0] | (v[..., 1] << 10), (v[..., 2] << 20) | _OPAQUE, out=out)
'''
    new = '''def pack_rgba10(rgba: torch.Tensor, out: torch.Tensor) -> torch.Tensor:
    """HxWx4 RGBA (uint8, or float16 0..1) -> `out`, HxW int32 of NVIDIA's packed RGB10A2: R in bits 0-9, G 10-19,
    B 20-29, alpha 30-31 (opaque). uint8 values are widened exactly (v10 = v8 * 1023 / 255); floats rounded.

    The scratch images are kept. The bits are the same as packing a fresh copy, and a float frame is not changed.
    """
    src = rgba[..., :3]
    v = getattr(pack_rgba10, "_v", None)
    if v is None or v.shape != src.shape or v.device != src.device or v.dtype != torch.int32:
        v = torch.empty(src.shape, dtype=torch.int32, device=src.device)
        pack_rgba10._v = v
    if src.is_floating_point():
        f = src.float()  # a copy for float16; float32 would be the frame itself, which must not be clamped
        if f.data_ptr() == src.data_ptr():
            f = f.clone()
        v.copy_(f.clamp_(0.0, 1.0).mul_(1023.0).round_())
    else:
        v.copy_(src)
        top = getattr(pack_rgba10, "_top", None)
        if top is None or top.shape != v.shape or top.device != v.device:
            top = torch.empty_like(v)
            pack_rgba10._top = top
        torch.bitwise_right_shift(v, 6, out=top)  # the original 8-bit value, before the shift below
        v.bitwise_left_shift_(2)
        v.bitwise_or_(top)  # (v << 2) | (v >> 6), 255 -> 1023
    plane = getattr(pack_rgba10, "_plane", None)
    if plane is None or plane.shape != out.shape or plane.device != out.device:
        plane = torch.empty_like(out)
        pack_rgba10._plane = plane
    out.copy_(v[..., 0])  # R
    torch.bitwise_left_shift(v[..., 1], 10, out=plane)
    out.bitwise_or_(plane)  # G
    torch.bitwise_left_shift(v[..., 2], 20, out=plane)
    plane.bitwise_or_(_OPAQUE)  # B, and opaque in the top two bits
    out.bitwise_or_(plane)
    return out
'''
    if old not in text:
        raise SystemExit("speed patch could not find the VSR pack")
    return text.replace(old, new, 1)


def patch_window(text: str) -> str:
    """Fast motion redraws the upscaled picture. It does not run the upscaler again."""
    if "def repeat(" in text:
        return text
    old = "        return cmd\n\n    def _uhd_fbo(self, w: int, h: int) -> int:\n"
    new = '''        return cmd

    def repeat(self, text: str | None = None):
        """Draw the picture already upscaled, then show it. No new frame and no upscaler.

        Fast motion fills its in-between slots with this. Running the upscaler again dropped the
        frame rate, and a different upscaler on those slots flashed. Returns True when it drew,
        a command string when the window has one, or False when there is no picture yet.
        """
        if self._uhd is None:
            return False
        GL, glfw = self.GL, self.glfw
        fw, fh, s, dw, dh, x0, y0 = self._layout()
        screen = self._target()
        ux, uy, uw, uh = self._ui_rect(fw, fh, dw, dh, x0, y0)
        pano = self._panorama_on(fw, fh)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, screen)
        GL.glViewport(0, 0, fw, fh)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        self._fit_uhd(screen, pano, fw, fh, dw, dh, x0, y0)
        if self.overlay and text:
            self._draw_stats(text, ux, uy, uw, uh)
        self._draw_ui(ux, uy, uw, uh)
        self._present()
        self.settle()
        self._events()
        if self.remote is not None:
            r = self.remote.poll()
            if r == "quit" or (r == "restart" and self.cmd is None):
                self.cmd = r
        if glfw.window_should_close(self.win):
            return "quit"
        cmd, self.cmd = self.cmd, None
        return True if cmd is None else cmd

    def _uhd_fbo(self, w: int, h: int) -> int:
'''
    if old not in text:
        raise SystemExit("speed patch could not find the window present")
    text = text.replace(old, new, 1)
    old = '''                fn(generated)

    def write(self, frame, text: str | None = None):
        cmd = None
        for s in self.sinks:
'''
    new = '''                fn(generated)

    def repeat(self, text: str | None = None):
        """The window redraws its picture. A sink with no picture yet is left out."""
        drew, cmd = False, None
        for s in self.sinks:
            fn = getattr(s, "repeat", None)
            if fn is None or fn is self.repeat:
                continue
            got = fn(text)
            if got is False:
                continue
            drew = True
            if got is not True:
                cmd = got or cmd
        if cmd is not None:
            return cmd
        return True if drew else False

    def write(self, frame, text: str | None = None):
        cmd = None
        for s in self.sinks:
'''
    if old not in text:
        raise SystemExit("speed patch could not find the combined sink")
    return text.replace(old, new, 1)


if __name__ == "__main__":
    main()

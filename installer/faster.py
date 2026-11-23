"""200 Mbps, and the frame check overlaps the draw. Safe to run twice, after use_vsr.py."""
from pathlib import Path


def patch_rate(text: str) -> str:
    """Ask chiaki-ng for 200 Mbps, on the settings key it actually reads."""
    text = text.replace("const int bitrate = 100000;", "const int bitrate = 200000;")
    text = text.replace("H.265, 100 Mbps", "H.265, 200 Mbps")
    text = text.replace("H.265 100 Mbps", "H.265 200 Mbps")
    text = text.replace("H.265 and 100 Mbps", "H.265 and 200 Mbps")
    text = text.replace(
        "That is the highest Remote Play size, and the top of chiaki-ng's bitrate slider.",
        "That is the highest Remote Play size. chiaki-ng's slider stops at 100; Keel writes 200, "
        "which chiaki-ng sends on. The console may send less.",
    )
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
        (root / "installer" / "launcher" / "PlayLaunch.cs", (patch_rate,)),
        (root / "installer" / "launcher" / "Ui.cs", (patch_rate,)),
        (root / "framegen" / "pipeline.py", (_overlap,)),
        (root / "framegen" / "cadence.py", (patch_cadence,)),
        (root / "framegen" / "chiaki_tap.py", (patch_color,)),
        (root / "framegen" / "vfg.py", (patch_vfg,)),
    ):
        text = path.read_text(encoding="utf-8")
        for fn in fns:
            text = fn(text)
        path.write_text(text, encoding="utf-8")
    print("200 Mbps, and the frame check overlaps the draw")


if __name__ == "__main__":
    main()


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
    """Reuse the buffers VFG packs and unpacks, instead of allocating them every frame."""
    if "def _pack_into(" in text:
        return text
    pairs = (
        ('''        self.steady, self.hud, self.real = 0.0, None, None
        self._slot: int | None = None  # the newest prepared slot
''', '''        self.steady, self.hud, self.real = 0.0, None, None
        self._slot: int | None = None  # the newest prepared slot
        self._pack_v = None  # int32 copy of a real frame, kept so packing does not allocate one
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
            v.copy_(bgr.float().clamp(0.0, 1.0).mul_(1023.0).round_())
        else:
            v.copy_(bgr)
            v.copy_((v << 2) | (v >> 6))  # 8 -> 10 bits, 255 -> 1023
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
    return text.replace(close, methods + close, 1)

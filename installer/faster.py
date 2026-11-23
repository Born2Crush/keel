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
        '@"Software\\Chiaki\\\\" + app + @"\\settings"',
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
    ):
        text = path.read_text(encoding="utf-8")
        for fn in fns:
            text = fn(text)
        path.write_text(text, encoding="utf-8")
    print("200 Mbps, and the frame check overlaps the draw")


if __name__ == "__main__":
    main()

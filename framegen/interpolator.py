"""RIFE frame interpolator tuned for a live stream: 1 to 3 in-between frames per real frame (60, 90 or 120 fps).

Design notes
------------
* Every buffer is pre-allocated at a fixed resolution, so the per-frame work can
  be captured once as CUDA graphs and replayed (removes Python/launch overhead,
  which matters more than FLOPs for these small networks).
* RIFE v4.2x splits work into `encode(frame) -> features` and
  `flownet(img0, img1, f0, f1) -> middle frame`. In a stream each real frame is
  the "next" frame of one pair and the "previous" frame of the following pair,
  so we encode it exactly once into one of two ping-pong slots.
* Frames enter and leave as HxWx3 uint8 BGR (OpenCV / capture-card layout).
"""

from __future__ import annotations

import importlib
import math
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import torch
import torch.nn.functional as F

from .fastrife import FastIFNet
from .sync import CAPTURE_LOCK

# version -> (module name, padding modulo). Modulo values match vs-rife.
MODELS: dict[str, tuple[str, int]] = {
    "4.22.lite": ("IFNet_HDv3_v4_22_lite", 32),
    "4.25.lite": ("IFNet_HDv3_v4_25_lite", 128),
    "4.25": ("IFNet_HDv3_v4_25", 64),
    "4.26": ("IFNet_HDv3_v4_26", 64),
}
WEIGHTS_URL = "https://github.com/HolyWu/vs-rife/releases/download/model/flownet_v{version}.pkl"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
# Steady HUD: the largest difference between the two real frames (0..1 per colour channel) at which a pixel still
# counts as unchanged. 0.03 is about 8 of 255 levels: above Remote Play's compression noise on a still HUD.
STEADY_HUD_THRESHOLD = float(os.environ.get("FRAMEGEN_STEADY_THRESHOLD", "0.03"))  # env: for tuning only
# ...and only within this many pixels of an edge: Sobel L1 of brightness (0..1) above STEADY_HUD_EDGE, about a
# 32-level step. See _steady.
STEADY_HUD_EDGE = 128.0 / 255.0
STEADY_HUD_EDGE_R = 1


def ensure_weights(version: str, model_dir: Path = DEFAULT_MODEL_DIR) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"flownet_v{version}.pkl"
    if not path.exists():
        url = WEIGHTS_URL.format(version=version)
        print(f"[rife] downloading {url}")
        tmp = path.with_suffix(".part")
        try:
            urllib.request.urlretrieve(url, tmp)
        except urllib.error.URLError:  # e.g. Python's CA bundle can't verify; Windows curl uses the OS store
            subprocess.run(["curl", "-sfL", "-o", str(tmp), url], check=True)
        tmp.replace(path)
    return path


class RifeInterpolator:
    def __init__(
        self,
        width: int,
        height: int,
        version: str = "4.22.lite",
        scale: float = 1.0,
        fp16: bool = True,
        cuda_graph: bool = True,
        device: str = "cuda",
        reference: bool = False,
        backend: str = "torch",
        build_engine: bool = True,
    ) -> None:
        """`reference=True` runs the unmodified vs-rife forward (for validating FastIFNet).
        backend="trt" runs the flow network as a TensorRT engine (built and cached on first use; with
        build_engine=False a missing engine raises FileNotFoundError instead)."""
        if version not in MODELS:
            raise ValueError(f"unknown model {version!r}; choose from {list(MODELS)}")
        if scale not in (0.25, 0.5, 1.0, 2.0):
            raise ValueError("scale must be 0.25, 0.5, 1.0 or 2.0")

        self.w, self.h = width, height
        self.version, self.scale = version, scale
        self.device = torch.device(device)
        self.dtype = torch.half if fp16 else torch.float
        torch.backends.cudnn.benchmark = True

        module_name, modulo = MODELS[version]
        mod = importlib.import_module(f"framegen.rife.{module_name}")
        state = torch.load(ensure_weights(version), map_location="cpu", weights_only=True)
        state = {k.replace("module.", ""): v for k, v in state.items() if k.startswith("module.")}

        self.flownet = mod.IFNet(scale=scale)
        # Checkpoints also carry training-only weights (teacher/caltime); ignore those, but every
        # parameter the inference model has must be present.
        missing = self.flownet.load_state_dict(state, strict=False).missing_keys
        if missing:
            raise RuntimeError(f"RIFE {version}: checkpoint is missing {missing[:5]}...")
        self.encode = mod.Head()
        self.encode.load_state_dict({k[len("encode."):]: v for k, v in state.items() if k.startswith("encode.")})
        for m in (self.flownet, self.encode):
            m.eval().to(self.device, self.dtype)

        # Pad to a multiple the pyramid can divide cleanly.
        step = max(modulo, int(modulo / scale))
        self.pw = math.ceil(width / step) * step
        self.ph = math.ceil(height / step) * step
        self.pad = (0, self.pw - width, 0, self.ph - height)

        dev, dt, ph, pw = self.device, self.dtype, self.ph, self.pw
        self.flow_div = torch.tensor([(pw - 1.0) / 2.0, (ph - 1.0) / 2.0], dtype=torch.float, device=dev)
        gx = torch.linspace(-1.0, 1.0, pw, device=dev).view(1, 1, 1, pw).expand(1, 1, ph, pw)
        gy = torch.linspace(-1.0, 1.0, ph, device=dev).view(1, 1, ph, 1).expand(1, 1, ph, pw)
        self.grid = torch.cat([gx, gy], 1).contiguous()
        self.timestep = torch.full((1, 1, ph, pw), 0.5, dtype=dt, device=dev)
        self._t = 0.5
        # Steady HUD (see _steady): read by the captured graphs, so changing them needs no recapture.
        self.steady = 0.0
        self.t_now = torch.full((1, 1, 1, 1), 0.5, device=dev)
        self.still_thr = torch.full((1, 1, 1, 1), STEADY_HUD_THRESHOLD, device=dev)
        gx = torch.tensor([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]], device=dev)
        self.sobel = torch.stack([gx, gx.t()]).unsqueeze(1).half()  # (2, 1, 3, 3): x and y gradients
        self.luma = torch.tensor([0.299, 0.587, 0.114], device=dev).view(1, 3, 1, 1).half()  # RGB
        self.edge = [torch.zeros((1, 1, ph, pw), dtype=torch.half, device=dev) for _ in range(2)]  # see _edges
        self.steady_w = torch.zeros((1, 1, ph, pw), device=dev)  # see _steady_mask
        self._slot: int | None = None  # the newest prepared slot

        # Static I/O buffers (graph inputs/outputs).
        self.in_u8 = torch.zeros((height, width, 3), dtype=torch.uint8, device=dev)
        self.out_u8 = torch.zeros((height, width, 3), dtype=torch.uint8, device=dev)
        with torch.no_grad():
            fshape = self.encode(torch.zeros((1, 3, ph, pw), dtype=dt, device=dev)).shape
        self.fast = None if reference else FastIFNet(self.flownet, ph, pw, dt, dev)
        if self.fast is not None:  # per slot: fp32 [img | features]
            self.src = [torch.zeros((1, 3 + fshape[1], ph, pw), device=dev) for _ in range(2)]
        else:
            self.img = [torch.zeros((1, 3, ph, pw), dtype=dt, device=dev) for _ in range(2)]
            self.feat = [torch.zeros(fshape, dtype=dt, device=dev) for _ in range(2)]

        self.trt = None
        if backend == "trt":
            if self.fast is None:
                raise ValueError("backend='trt' needs the fast path (reference=False)")
            from .trt import TrtIFNet
            self.trt = TrtIFNet(self.fast, tuple(self.src[0].shape), f"{version}_s{scale}", dev, build=build_engine)
        elif backend != "torch":
            raise ValueError(f"unknown backend {backend!r}")

        self._graphs: dict[tuple, torch.cuda.CUDAGraph] = {}
        self.cuda_graph = cuda_graph
        if cuda_graph:
            for slot in (0, 1):
                self._graphs[("prep", slot)] = self._capture(lambda s=slot: self._prepare_eager(s))
                self._graphs[("mask", slot)] = self._capture(lambda s=slot: self._steady_mask(1 - s, s))
                for steady in (False, True):  # two variants, so Steady HUD costs nothing when it's off
                    self._graphs[("interp", slot, steady)] = self._capture(
                        lambda s=slot, st=steady: self._interp_eager(1 - s, s, st))

    # ---- eager kernels (also what gets captured into graphs) -----------------
    @torch.no_grad()
    def _prepare_eager(self, slot: int) -> None:
        x = self.in_u8.permute(2, 0, 1).flip(0).unsqueeze(0).to(self.dtype).div_(255.0)  # BGR->RGB, 0..1
        if self.pad[1] or self.pad[3]:
            x = F.pad(x, self.pad)
        if self.fast is not None:
            self.src[slot].copy_(FastIFNet.make_src(x, self.encode(x)))
        else:
            self.img[slot].copy_(x)
            self.feat[slot].copy_(self.encode(x))

    @torch.no_grad()
    def _interp_eager(self, a: int, b: int, steady: bool = False) -> None:
        if self.trt is not None:
            mid = self.trt(self.src[a], self.src[b])
        elif self.fast is not None:
            mid = self.fast(self.src[a], self.src[b])
        else:
            mid = self.flownet(
                self.img[a], self.img[b], self.timestep, self.flow_div, self.grid, self.feat[a], self.feat[b]
            )
        if steady:
            mid = self._steady(mid, a, b)
        mid = mid[0, :, : self.h, : self.w].flip(0).permute(1, 2, 0)  # RGB->BGR, HWC
        self.out_u8.copy_(mid.clamp(0.0, 1.0).mul(255.0).round_().to(torch.uint8))

    def _rgb(self, slot: int) -> torch.Tensor:
        """Slot's real frame, (1, 3, ph, pw) RGB 0..1."""
        return (self.src[slot][:, :3] if self.fast is not None else self.img[slot]).float()

    def _steady(self, mid: torch.Tensor, a: int, b: int) -> torch.Tensor:
        """Steady HUD: where a pixel is (almost) the same in both real frames, like a map, health bar or subtitle
        over a moving scene, the AI frame takes the real pixel (blended at t, so fades still fade) instead of the
        model's, which can drag it along with the motion behind it. The edge is soft: fully real below half the
        threshold, fully the model's above it, so there's no speckle where the difference hovers near it.

        Only near edges (within STEADY_HUD_EDGE_R pixels of a brightness step of about 32 levels, in either real
        frame): text, icons and map lines, where wobble shows. On a flat background the model's pixel is kept, so a
        pole or a car crossing a sky or road that matches before and after isn't painted over. On eight 60 fps
        gameplay clips (scripts/steady_hud_eval.py) that keeps 96% of the fix on HUD-like pixels at a quarter of the
        cost elsewhere.

        Where it acts (steady_w) depends only on the two real frames, so _steady_mask works it out once per real
        frame (after prepare) and each in-between frame just blends."""
        return torch.lerp(mid.float(), torch.lerp(self._rgb(a), self._rgb(b), self.t_now), self.steady_w)

    @torch.no_grad()
    def _edges(self, slot: int) -> None:
        """edge[slot]: 1 on a brightness edge of that real frame (worked out once per frame, in half precision)."""
        lum = F.conv2d(self._rgb(slot).half(), self.luma)
        g = F.conv2d(lum, self.sobel, padding=1).abs_().sum(1, keepdim=True)
        self.edge[slot].copy_(g > STEADY_HUD_EDGE)

    @torch.no_grad()
    def _steady_mask(self, a: int, b: int) -> None:
        """steady_w for real frames a -> b: 1 where Steady HUD takes the real pixels, 0 where the model's stay.
        The newest frame's edges are worked out here; the older one's were, when it was the newest."""
        self._edges(b)
        ia, ib = self._rgb(a).half(), self._rgb(b).half()
        d = (ib - ia).abs_().amax(1, keepdim=True)
        thr = self.still_thr.half()
        w = ((thr - d) * (2.0 / thr)).clamp_(0.0, 1.0)
        r = STEADY_HUD_EDGE_R
        self.steady_w.copy_(w * F.max_pool2d(torch.maximum(self.edge[a], self.edge[b]), 2 * r + 1, 1, r))

    def set_steady(self, threshold: float) -> None:
        """Steady HUD on (threshold: largest real-frame difference, 0..1, that counts as still) or off (0)."""
        if threshold > 0:
            self.still_thr.fill_(threshold)
            if self.steady <= 0 and self._slot is not None:  # turned on mid-pair: the current pair's mask now
                self._edges(1 - self._slot)  # (the older frame's edges weren't worked out while it was off)
                self._steady_mask(1 - self._slot, self._slot)
        self.steady = threshold

    def _capture(self, fn) -> torch.cuda.CUDAGraph:
        with CAPTURE_LOCK:
            return self._capture_locked(fn)

    def _capture_locked(self, fn) -> torch.cuda.CUDAGraph:
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(3):  # warm-up (cudnn autotune, allocator) before capture
                fn()
        torch.cuda.current_stream().wait_stream(side)
        g = torch.cuda.CUDAGraph()
        # thread_local, and CAPTURE_LOCK: the Remote Play reader decodes on the GPU in another thread. With the
        # default ("global") its CUDA calls fail during capture, and FFmpeg's decoder crashes on that.
        with torch.cuda.graph(g, capture_error_mode="thread_local"):
            fn()
        return g

    # ---- public API ----------------------------------------------------------
    def prepare(self, slot: int) -> None:
        """Convert+encode `in_u8` into ping-pong slot `slot` (0/1); with Steady HUD on, also where it acts between
        the previous real frame and this one."""
        if self.cuda_graph:
            self._graphs[("prep", slot)].replay()
            if self.steady > 0:
                self._graphs[("mask", slot)].replay()
        else:
            self._prepare_eager(slot)
            if self.steady > 0:
                self._steady_mask(1 - slot, slot)
        self._slot = slot

    def interpolate(self, newest_slot: int, t: float = 0.5) -> torch.Tensor:
        """Frame at fraction t (0.5 = midpoint) from slot (1-newest_slot) to newest_slot -> `out_u8`.
        Each call overwrites `out_u8`, so for several timesteps copy each result out before the next."""
        if t != self._t:  # the graphs read these static planes, so no recapture is needed
            if self.trt is not None:
                self.trt.set_timestep(t)
            if self.fast is not None:
                self.fast.set_timestep(t)
            else:
                self.timestep.fill_(t)
            self.t_now.fill_(t)
            self._t = t
        steady = self.steady > 0
        if self.cuda_graph:
            self._graphs[("interp", newest_slot, steady)].replay()
        else:
            self._interp_eager(1 - newest_slot, newest_slot, steady)
        return self.out_u8

    def load(self, frame_u8: torch.Tensor) -> None:
        """Copy an HxWx3 uint8 frame (GPU or pinned CPU) into the static input."""
        self.in_u8.copy_(frame_u8, non_blocking=True)

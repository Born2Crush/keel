"""NVIDIA RTX Video Super Resolution (RTX VSR) on CUDA frames, through the RTX Video SDK.

The SDK is NVIDIA's (free developer download, NVIDIA RTX SDKs licence) and is not part of this repo:
unpack it and point NV_RTX_VIDEO_SDK at that folder, then build the small
ctypes wrapper with native\\rtxvsr\\build.bat. The model itself (nvngx_vsr.dll) is loaded from the SDK's
bin folder; NVIDIA's runtime in the driver may update it.

Input is an 8-bit RGBA CUDA tensor; output goes to any 8-bit RGBA CUDA array (e.g. a mapped GL texture),
on PyTorch's current stream, so it orders with the interpolation and display work without CPU waits.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

import torch

from .glcuda import _cudart, cudart_path

ROOT = Path(__file__).resolve().parent.parent
SDK_DIR = Path(os.environ["NV_RTX_VIDEO_SDK"]) if os.environ.get("NV_RTX_VIDEO_SDK") else None
WRAPPER = ROOT / "native" / "rtxvsr" / "build" / "rtxvsr.dll"
DATA_DIR = ROOT / "out" / "ngx"  # NGX logs / caches

QUALITY = {"bicubic": 0, "low": 1, "medium": 2, "high": 3, "ultra": 4}

_MEMCPY_DEVICE_TO_DEVICE = 3
_ARRAY_SURFACE_LOAD_STORE = 0x02
_RESOURCE_TYPE_ARRAY = 0


class _ChannelDesc(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("z", ctypes.c_int), ("w", ctypes.c_int),
                ("f", ctypes.c_int)]


_RGBA8 = _ChannelDesc(8, 8, 8, 8, 1)  # cudaChannelFormatKindUnsigned


def _array_resource_desc(array: ctypes.c_void_p) -> ctypes.Array:
    """cudaResourceDesc for a CUDA array: resType at offset 0, the array handle at offset 8, rest zero.
    Over-allocated so newer, larger layouts of the struct read zeros."""
    buf = (ctypes.c_ubyte * 256)()
    ctypes.c_int.from_buffer(buf, 0).value = _RESOURCE_TYPE_ARRAY
    ctypes.c_void_p.from_buffer(buf, 8).value = array.value
    return buf


def _linear_texture_desc() -> ctypes.Array:
    """cudaTextureDesc: clamp addressing, linear filtering, normalised float reads, normalised coordinates
    (what the SDK's own CUDA sample uses for its input)."""
    buf = (ctypes.c_ubyte * 256)()
    for i in range(3):
        ctypes.c_int.from_buffer(buf, 4 * i).value = 1  # addressMode[i] = cudaAddressModeClamp
    ctypes.c_int.from_buffer(buf, 12).value = 1  # filterMode = cudaFilterModeLinear
    ctypes.c_int.from_buffer(buf, 16).value = 1  # readMode = cudaReadModeNormalizedFloat
    ctypes.c_int.from_buffer(buf, 40).value = 1  # normalizedCoords
    return buf


def unavailable_reason() -> str | None:
    """None if the SDK and the wrapper are in place, otherwise what is missing."""
    if SDK_DIR is None:
        # what people see (the installed app has no SDK); developers set NV_RTX_VIDEO_SDK to the unpacked SDK folder
        return ("it's not in this version of SmoovPlay: it needs NVIDIA's RTX Video SDK, which can't be included "
                "yet. AMD FSR 1 looks about the same")
    if not (SDK_DIR / "bin" / "Windows" / "x64" / "rel" / "nvngx_vsr.dll").exists():
        return f"RTX Video SDK not found at {SDK_DIR} (set NV_RTX_VIDEO_SDK)"
    if not WRAPPER.exists():
        return f"wrapper not built: run native\\rtxvsr\\build.bat ({WRAPPER} missing)"
    return None


class RtxVsr:
    """Upscales src_w x src_h RGBA frames into a caller-provided RGBA CUDA array with RTX VSR.

    Create it, and call it, on one thread (NGX isn't thread-safe), after PyTorch has initialised CUDA there.
    The CUDA stream current at creation is the one VSR runs on.
    """

    def __init__(self, src_w: int, src_h: int, quality: int = QUALITY["high"]) -> None:
        why = unavailable_reason()
        if why:
            raise RuntimeError(why)
        self.w, self.h, self.quality = src_w, src_h, int(quality)
        torch.zeros(1, device="cuda")  # CUDA context current on this thread
        self.rt = rt = _cudart()
        p, i, u, s = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_size_t
        rt.cudaMallocArray.argtypes = [ctypes.POINTER(p), ctypes.POINTER(_ChannelDesc), s, s, u]
        rt.cudaFreeArray.argtypes = [p]
        rt.cudaCreateTextureObject.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), p, p, p]
        rt.cudaDestroyTextureObject.argtypes = [ctypes.c_ulonglong]
        rt.cudaCreateSurfaceObject.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), p]
        rt.cudaDestroySurfaceObject.argtypes = [ctypes.c_ulonglong]
        rt.cudaMemcpy2DToArrayAsync.argtypes = [p, s, s, p, s, s, s, i, p]
        rt.cudaMemcpy2DFromArrayAsync.argtypes = [p, s, p, s, s, s, s, i, p]

        self.dll = dll = ctypes.CDLL(str(WRAPPER))
        dll.rtxvsr_set_cudart.argtypes = [ctypes.c_wchar_p]
        dll.rtxvsr_create.argtypes = [p, p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        dll.rtxvsr_evaluate.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong] + [u] * 6 + [i]
        self._ngx(dll.rtxvsr_set_cudart(cudart_path()), "load CUDA runtime")
        drv = ctypes.CDLL("nvcuda.dll")
        ctx = p()
        self._cuda(drv.cuCtxGetCurrent(ctypes.byref(ctx)), "get CUDA context")
        self.stream = torch.cuda.current_stream()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        rc = dll.rtxvsr_create(ctx, p(self.stream.cuda_stream),
                               str(SDK_DIR / "bin" / "Windows" / "x64" / "rel"), str(DATA_DIR))
        if rc == -1:
            raise RuntimeError("RTX VSR is not available on this GPU/driver (needs an RTX 20-series or newer)")
        if rc == -2:
            raise RuntimeError("RTX VSR needs a newer NVIDIA driver")
        self._ngx(rc, "create VSR")

        self.in_array = p()
        self._cuda(rt.cudaMallocArray(ctypes.byref(self.in_array), ctypes.byref(_RGBA8), src_w, src_h, 0),
                   "allocate input array")
        self.tex = ctypes.c_ulonglong()
        self._res_in, self._tex_desc = _array_resource_desc(self.in_array), _linear_texture_desc()
        self._cuda(rt.cudaCreateTextureObject(ctypes.byref(self.tex), self._res_in, self._tex_desc, None),
                   "create input texture")
        self._surfaces: dict[int, ctypes.c_ulonglong] = {}  # output array handle -> surface object
        self._events: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
        self.gpu_ms: list[float] = []

    def _cuda(self, err: int, what: str) -> None:
        if err:
            raise RuntimeError(f"RTX VSR: {what} failed (CUDA error {err})")

    def _ngx(self, rc: int, what: str) -> None:
        if rc:
            raise RuntimeError(f"RTX VSR: {what} failed (code {rc & 0xFFFFFFFF:#x})")

    def surface(self, array: ctypes.c_void_p) -> ctypes.c_ulonglong:
        """Surface object for an output array (cached; arrays must be 8-bit RGBA with surface load/store)."""
        key = array.value
        if key not in self._surfaces:
            surf = ctypes.c_ulonglong()
            self._cuda(self.rt.cudaCreateSurfaceObject(ctypes.byref(surf), _array_resource_desc(array)),
                       "create output surface")
            self._surfaces[key] = surf
        return self._surfaces[key]

    def forget(self, array: ctypes.c_void_p) -> None:
        """Drop the cached surface of an output array that is about to be freed or re-registered."""
        surf = self._surfaces.pop(array.value, None)
        if surf is not None:
            self.rt.cudaDestroySurfaceObject(surf)

    def __call__(self, rgba: torch.Tensor, out_array: ctypes.c_void_p, out_w: int, out_h: int,
                 quality: int | None = None) -> None:
        """Upscale rgba (h x w x 4 uint8, contiguous CUDA) into out_array's top-left out_w x out_h."""
        stream = ctypes.c_void_p(self.stream.cuda_stream)
        self._cuda(self.rt.cudaMemcpy2DToArrayAsync(self.in_array, 0, 0, ctypes.c_void_p(rgba.data_ptr()),
                                                    self.w * 4, self.w * 4, self.h,
                                                    _MEMCPY_DEVICE_TO_DEVICE, stream), "copy input")
        start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        start.record(self.stream)
        self._ngx(self.dll.rtxvsr_evaluate(self.tex, self.surface(out_array), self.w, self.h, 0, 0, out_w, out_h,
                                           self.quality if quality is None else int(quality)), "evaluate")
        end.record(self.stream)
        self._events.append((start, end))
        self._collect()

    def _collect(self) -> None:
        """GPU time of finished evaluations (read without waiting)."""
        while self._events and self._events[0][1].query():
            s, e = self._events.pop(0)
            self.gpu_ms.append(s.elapsed_time(e))

    def new_output_array(self, w: int, h: int) -> ctypes.c_void_p:
        """A CUDA-owned RGBA8 array usable as output (for tests and offscreen use)."""
        arr = ctypes.c_void_p()
        self._cuda(self.rt.cudaMallocArray(ctypes.byref(arr), ctypes.byref(_RGBA8), w, h,
                                           _ARRAY_SURFACE_LOAD_STORE), "allocate output array")
        return arr

    def read_array(self, arr: ctypes.c_void_p, w: int, h: int) -> torch.Tensor:
        """Copy an RGBA8 array into a new h x w x 4 CUDA tensor (stream-ordered)."""
        out = torch.empty((h, w, 4), dtype=torch.uint8, device="cuda")
        self._cuda(self.rt.cudaMemcpy2DFromArrayAsync(ctypes.c_void_p(out.data_ptr()), w * 4, arr, 0, 0, w * 4, h,
                                                      _MEMCPY_DEVICE_TO_DEVICE,
                                                      ctypes.c_void_p(self.stream.cuda_stream)), "read output")
        return out

    def free_array(self, arr: ctypes.c_void_p) -> None:
        self.forget(arr)
        self.rt.cudaFreeArray(arr)

    def close(self) -> None:
        torch.cuda.synchronize()
        self._collect()
        for surf in self._surfaces.values():
            self.rt.cudaDestroySurfaceObject(surf)
        self._surfaces.clear()
        if self.tex.value:
            self.rt.cudaDestroyTextureObject(self.tex)
            self.tex = ctypes.c_ulonglong()
        if self.in_array.value:
            self.rt.cudaFreeArray(self.in_array)
            self.in_array = ctypes.c_void_p()
        self.dll.rtxvsr_shutdown()

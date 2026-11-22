"""NVIDIA DLSS Super Resolution for Keel's 4K output.

Remote Play arrives at 1080p. Frame generation stays at that size. Output size 4K asks DLSS
to draw 3840x2160. That scale is exactly 2x, which is DLSS Performance mode (preset M is
NVIDIA's default for Performance). The stream has no engine motion vectors, so each frame is
reset and upscaled on its own.

The DLSS library is NVIDIA's (nvngx_dlss.dll), loaded from the driver or from KEEL_DLSS_DLL.
Keel does not ship it and does not charge for it. NVIDIA's application id goes in
KEEL_DLSS_PROJECT_ID; that id is from NVIDIA's DLSS SDK, not a Keel account.

NVIDIA's own SDK timing, Performance mode, 4K output, preset M: about 2.1 ms a frame on an
RTX 4080, and about 11 ms on an RTX 3060. Not measured inside Keel.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .output_size import UHD_H, UHD_W

# NVSDK_NGX_PerfQuality_Value_MaxPerf. Quality is 1.5x, Balanced ~1.7x, Performance is 2x.
PERFORMANCE = 0
FEATURE_SUPER_SAMPLING = 1  # NVSDK_NGX_Feature_SuperSampling
FLAG_AUTO_EXPOSURE = 1 << 6
RGB8UI = 1  # NVSDK_NGX_Buffer_Format_RGB8UI
API_VERSION = 0x15  # NVSDK_NGX_VERSION_API_MACRO in the public SDK header
SUCCESS = 0x1
ENGINE_CUSTOM = 0


def uses_dlss(output_size: str, src_w: int, src_h: int) -> bool:
    """Output size 4K upscales a stream smaller than 4K with DLSS, not with FSR."""
    return output_size == "2160p" and (src_w < UHD_W or src_h < UHD_H)


def find_dll() -> Path | None:
    """nvngx_dlss.dll: KEEL_DLSS_DLL, else the usual NVIDIA driver locations."""
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
    ]
    tails = (
        r"NVIDIA Corporation\NVIDIA NGX\nvngx_dlss.dll",
        r"NVIDIA Corporation\NVIDIA App\nvngx_dlss.dll",
    )
    for root in roots:
        for tail in tails:
            path = Path(root) / tail
            if path.is_file():
                return path
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvngx_dlss.dll"
    return system if system.is_file() else None


def unavailable_reason() -> str | None:
    """None when DLSS can be started. Otherwise one sentence for the log and the menu."""
    if sys.platform != "win32":
        return "DLSS runs on Windows with an NVIDIA RTX card"
    if find_dll() is None:
        return ("nvngx_dlss.dll was not found. It comes with the NVIDIA driver; "
                "set KEEL_DLSS_DLL to that file")
    if not os.environ.get("KEEL_DLSS_PROJECT_ID"):
        return ("set KEEL_DLSS_PROJECT_ID to the application id from NVIDIA's DLSS SDK "
                "(free from NVIDIA; Keel has no account and no fee)")
    try:
        import torch
    except ImportError:
        return "DLSS needs PyTorch with CUDA, on an NVIDIA RTX card"
    if not torch.cuda.is_available():
        return "DLSS needs an NVIDIA RTX card"
    if torch.cuda.get_device_capability(0) < (7, 5):
        return "DLSS needs an NVIDIA RTX card (Turing or newer)"
    return None


class DlssScaler:
    """1080p RGB in, 3840x2160 RGB out, through nvngx_dlss.dll. One feature for the process."""

    def __init__(self, dll: Path, project_id: str) -> None:
        import ctypes
        self._ctypes = ctypes
        self.lib = ctypes.CDLL(str(dll))
        self._bind()
        folder = Path(tempfile.gettempdir()) / "keel-ngx"
        folder.mkdir(parents=True, exist_ok=True)
        rc = self.lib.NVSDK_NGX_CUDA_Init_with_ProjectID(
            project_id.encode(), ENGINE_CUSTOM, b"keel", str(folder), None, API_VERSION)
        if rc != SUCCESS:
            raise RuntimeError(f"DLSS init failed (NGX 0x{rc:08x})")
        params = ctypes.c_void_p()
        rc = self.lib.NVSDK_NGX_CUDA_GetCapabilityParameters(ctypes.byref(params))
        if rc != SUCCESS or not params:
            raise RuntimeError(f"DLSS parameters failed (NGX 0x{rc:08x})")
        self.params = params
        self.handle = ctypes.c_void_p()
        self._src = (0, 0)
        self._zeros = None

    def _bind(self) -> None:
        c, lib = self._ctypes, self.lib
        void, u32, i32 = c.c_void_p, c.c_uint, c.c_int
        lib.NVSDK_NGX_CUDA_Init_with_ProjectID.argtypes = [
            c.c_char_p, i32, c.c_char_p, c.c_wchar_p, void, i32]
        lib.NVSDK_NGX_CUDA_Init_with_ProjectID.restype = u32
        lib.NVSDK_NGX_CUDA_GetCapabilityParameters.argtypes = [c.POINTER(void)]
        lib.NVSDK_NGX_CUDA_GetCapabilityParameters.restype = u32
        lib.NVSDK_NGX_Parameter_SetUI.argtypes = [void, c.c_char_p, u32]
        lib.NVSDK_NGX_Parameter_SetUI.restype = u32
        lib.NVSDK_NGX_Parameter_SetI.argtypes = [void, c.c_char_p, i32]
        lib.NVSDK_NGX_Parameter_SetI.restype = u32
        lib.NVSDK_NGX_Parameter_SetF.argtypes = [void, c.c_char_p, c.c_float]
        lib.NVSDK_NGX_Parameter_SetF.restype = u32
        lib.NVSDK_NGX_Parameter_SetVoidPointer.argtypes = [void, c.c_char_p, void]
        lib.NVSDK_NGX_Parameter_SetVoidPointer.restype = u32
        lib.NVSDK_NGX_CUDA_CreateFeature.argtypes = [i32, void, c.POINTER(void)]
        lib.NVSDK_NGX_CUDA_CreateFeature.restype = u32
        lib.NVSDK_NGX_CUDA_EvaluateFeature_C.argtypes = [void, void, void]
        lib.NVSDK_NGX_CUDA_EvaluateFeature_C.restype = u32

    def _feature(self, w: int, h: int) -> None:
        if self._src == (w, h):
            return
        p = self.params
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Width", w)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Height", h)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"OutWidth", UHD_W)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"OutHeight", UHD_H)
        self.lib.NVSDK_NGX_Parameter_SetI(p, b"PerfQualityValue", PERFORMANCE)
        self.lib.NVSDK_NGX_Parameter_SetI(p, b"RTXValue", 0)
        self.lib.NVSDK_NGX_Parameter_SetI(p, b"DLSS.Feature.Create.Flags", FLAG_AUTO_EXPOSURE)
        rc = self.lib.NVSDK_NGX_CUDA_CreateFeature(
            FEATURE_SUPER_SAMPLING, p, self._ctypes.byref(self.handle))
        if rc != SUCCESS:
            raise RuntimeError(f"DLSS feature failed (NGX 0x{rc:08x})")
        self._src = (w, h)

    def upscale(self, bgr) -> "object":
        """BGR uint8 CUDA tensor (or a CPU array) at the stream size -> 3840x2160 BGR on CUDA."""
        import torch
        if not torch.is_tensor(bgr):
            bgr = torch.from_numpy(bgr)
        if bgr.device.type != "cuda":
            bgr = bgr.cuda()
        bgr = bgr.contiguous()
        h, w = int(bgr.shape[0]), int(bgr.shape[1])
        self._feature(w, h)
        rgb = bgr.flip(-1).contiguous()
        out = torch.empty((UHD_H, UHD_W, 3), dtype=torch.uint8, device="cuda")
        if self._zeros is None or self._zeros.shape[0] != h * w * 2:
            self._zeros = torch.zeros(h * w * 2, dtype=torch.float32, device="cuda")
        p = self.params
        color = self._ctypes.c_void_p(rgb.data_ptr())
        dest = self._ctypes.c_void_p(out.data_ptr())
        motion = self._ctypes.c_void_p(self._zeros.data_ptr())
        self.lib.NVSDK_NGX_Parameter_SetVoidPointer(p, b"Color", color)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Color.Format", RGB8UI)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Color.SizeInBytes", w * h * 3)
        self.lib.NVSDK_NGX_Parameter_SetVoidPointer(p, b"Output", dest)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Output.Format", RGB8UI)
        self.lib.NVSDK_NGX_Parameter_SetUI(p, b"Output.SizeInBytes", UHD_W * UHD_H * 3)
        self.lib.NVSDK_NGX_Parameter_SetVoidPointer(p, b"MotionVectors", motion)
        self.lib.NVSDK_NGX_Parameter_SetF(p, b"Jitter.Offset.X", 0.0)
        self.lib.NVSDK_NGX_Parameter_SetF(p, b"Jitter.Offset.Y", 0.0)
        self.lib.NVSDK_NGX_Parameter_SetF(p, b"MV.Scale.X", 1.0)
        self.lib.NVSDK_NGX_Parameter_SetF(p, b"MV.Scale.Y", 1.0)
        self.lib.NVSDK_NGX_Parameter_SetI(p, b"Reset", 1)  # no engine motion vectors: don't accumulate
        rc = self.lib.NVSDK_NGX_CUDA_EvaluateFeature_C(self.handle, p, None)
        if rc != SUCCESS:
            raise RuntimeError(f"DLSS evaluate failed (NGX 0x{rc:08x})")
        return out.flip(-1).contiguous()


_session: tuple | None = None


def session() -> tuple[DlssScaler | None, str | None]:
    """The process-wide DLSS scaler, or (None, why it isn't available)."""
    global _session
    if _session is not None:
        return _session  # type: ignore[return-value]
    why = unavailable_reason()
    if why:
        _session = (None, why)
        return _session
    try:
        scaler = DlssScaler(find_dll(), os.environ["KEEL_DLSS_PROJECT_ID"])
    except (AttributeError, OSError, RuntimeError) as e:
        _session = (None, str(e)[:200])
        return _session
    _session = (scaler, None)
    return _session

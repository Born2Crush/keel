"""NVIDIA DLSS Super Resolution for Keel's 4K output.

Remote Play arrives at 1080p. Frame generation stays at that size. Output size 4K asks DLSS
to draw 3840x2160. That scale is exactly 2x, which is DLSS Performance mode (preset M is
NVIDIA's default for Performance). The stream has no engine motion vectors, so each frame is
reset and upscaled on its own.

The CUDA entry points live in the driver's NGX runtime (_nvngx.dll). nvngx_dlss.dll is the
feature library that runtime loads; the driver installs both. Keel does not ship either
and does not charge for them. A custom engine's project id only has to look like a GUID
(NVIDIA's DLSS programming guide). KEEL_DLSS_PROJECT_ID overrides Keel's.

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
# NVIDIA's guide: for a custom engine the driver only checks that this looks like a GUID.
PROJECT_ID = "7e1c4a90-2b6d-4f18-9c35-8a0d6e2b4f71"


def uses_dlss(output_size: str, src_w: int, src_h: int) -> bool:
    """Output size 4K upscales a stream smaller than 4K with DLSS, not with FSR."""
    return output_size == "2160p" and (src_w < UHD_W or src_h < UHD_H)


def project_id() -> str:
    """The GUID-like id NGX wants. The environment overrides Keel's own."""
    return os.environ.get("KEEL_DLSS_PROJECT_ID") or PROJECT_ID


def _first_file(candidates) -> Path | None:
    for path in candidates:
        if path is not None and Path(path).is_file():
            return Path(path)
    return None


def find_ngx() -> Path | None:
    """The driver's NGX runtime, which exports the CUDA DLSS calls.

    The feature file nvngx_dlss.dll does not. The driver records the runtime folder in
    HKLM\\...\\nvlddmkm\\NGXCore, value NGXPath.
    """
    candidates = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"System\CurrentControlSet\Services\nvlddmkm\NGXCore") as key:
                folder, _ = winreg.QueryValueEx(key, "NGXPath")
            candidates += [Path(folder) / "_nvngx.dll", Path(folder) / "nvngx.dll"]
        except OSError:
            pass
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    candidates += [system / "_nvngx.dll", system / "nvngx.dll"]
    return _first_file(candidates)


def find_dll() -> Path | None:
    """nvngx_dlss.dll, so NGX can load the feature. KEEL_DLSS_DLL, else the driver copies."""
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    roots = []
    for key in ("ProgramFiles", "ProgramW6432", "ProgramData"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    tails = (
        r"NVIDIA Corporation\NVIDIA NGX\nvngx_dlss.dll",
        r"NVIDIA Corporation\NVIDIA App\nvngx_dlss.dll",
        r"NVIDIA\NGX\models\nvngx_dlss.dll",
    )
    found = _first_file(root / tail for root in roots for tail in tails)
    if found is not None:
        return found
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvngx_dlss.dll"
    if system.is_file():
        return system
    # The driver drops versioned copies a few folders down. Stop at the first one.
    for root in roots:
        base = root / "NVIDIA Corporation"
        if not base.is_dir():
            continue
        for path in base.rglob("nvngx_dlss.dll"):
            return path
    return None


def unavailable_reason() -> str | None:
    """None when DLSS can be started. Otherwise one sentence for the log and the menu."""
    if sys.platform != "win32":
        return "DLSS runs on Windows with an NVIDIA RTX card"
    if find_ngx() is None:
        return "DLSS needs the NVIDIA driver runtime (_nvngx.dll). Update the NVIDIA driver."
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

    def __init__(self, ngx: Path, feature: Path | None, project: str) -> None:
        import ctypes
        self._ctypes = ctypes
        self._kept = []  # wide strings the feature-path struct points at
        if feature is not None and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(feature.parent))
        self.lib = ctypes.CDLL(str(ngx))
        self._bind()
        folder = Path(tempfile.gettempdir()) / "keel-ngx"
        folder.mkdir(parents=True, exist_ok=True)
        info = self._feature_info(feature)
        feature_info = None if info is None else ctypes.cast(ctypes.byref(info), ctypes.c_void_p)
        rc = self._init(project.encode(), ENGINE_CUSTOM, b"keel", str(folder), feature_info, API_VERSION)
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

    def _sym(self, *names: str):
        """The driver's export drops the 'with_' the C header uses."""
        for name in names:
            try:
                return getattr(self.lib, name)
            except AttributeError:
                continue
        raise AttributeError(names[0])

    def _feature_info(self, feature: Path | None):
        """Tell NGX where nvngx_dlss.dll is, besides the application folder."""
        if feature is None:
            return None
        c = self._ctypes
        path = c.c_wchar_p(str(feature.parent))
        slots = (c.c_wchar_p * 1)(path)

        class PathList(c.Structure):
            _fields_ = [("Path", c.POINTER(c.c_wchar_p)), ("Length", c.c_uint)]

        class Logging(c.Structure):
            _fields_ = [("LoggingCallback", c.c_void_p), ("MinimumLoggingLevel", c.c_int),
                        ("DisableOtherLoggingSinks", c.c_bool)]

        class Common(c.Structure):
            _fields_ = [("PathListInfo", PathList), ("InternalData", c.c_void_p), ("LoggingInfo", Logging)]

        info = Common()
        info.PathListInfo.Path = c.cast(slots, c.POINTER(c.c_wchar_p))
        info.PathListInfo.Length = 1
        self._kept += [path, slots, info]
        return info

    def _bind(self) -> None:
        c, lib = self._ctypes, self.lib
        void, u32, i32 = c.c_void_p, c.c_uint, c.c_int
        self._init = self._sym("NVSDK_NGX_CUDA_Init_with_ProjectID", "NVSDK_NGX_CUDA_Init_ProjectID")
        self._init.argtypes = [c.c_char_p, i32, c.c_char_p, c.c_wchar_p, void, i32]
        self._init.restype = u32
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
        self._eval = self._sym("NVSDK_NGX_CUDA_EvaluateFeature_C", "NVSDK_NGX_CUDA_EvaluateFeature")
        self._eval.argtypes = [void, void, void]
        self._eval.restype = u32

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
        """BGR uint8 CUDA tensor (or a CPU array) at the stream size -> 3840x2160 RGB on CUDA.

        Row 0 is the top of the picture. The display's upload turns that into the
        GL texture (RGBA, bottom row first) in one copy.
        """
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
        rc = self._eval(self.handle, p, None)
        if rc != SUCCESS:
            raise RuntimeError(f"DLSS evaluate failed (NGX 0x{rc:08x})")
        # RGB, row 0 at the top. The display copies this into the GL texture and
        # turns it upright there, so it is not flipped into BGR and back.
        return out


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
        import torch
        torch.cuda.init()  # NGX CUDA calls use the current context
        scaler = DlssScaler(find_ngx(), find_dll(), project_id())
    except (AttributeError, OSError, RuntimeError) as e:
        _session = (None, str(e)[:200])
        return _session
    _session = (scaler, None)
    return _session

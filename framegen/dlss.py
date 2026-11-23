from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .output_size import UHD_H, UHD_W

PERFORMANCE = 0
FEATURE_SUPER_SAMPLING = 1
FEATURE_IMAGE_SR = 3
FLAG_AUTO_EXPOSURE = 1 << 6
RGB8UI = 1
API_VERSION = 0x15
SUCCESS = 0x1
FAIL = 0xBAD00000
ENGINE_CUSTOM = 0
PRESET_K = 11
PRESET_L = 12
PRESET_M = 13
PROJECT_ID = "7e1c4a90-2b6d-4f18-9c35-8a0d6e2b4f71"

def uses_dlss(output_size, src_w, src_h, scaler=None):
    if src_w >= UHD_W and src_h >= UHD_H:
        return False
    return output_size == "2160p" or scaler == "dlss"

def ngx_failed(rc):
    return rc == 0 or (int(rc) & 0xFFF00000) == FAIL

def ngx_why(rc):
    return {1: "not supported", 4: "feature missing", 5: "bad parameter", 6: "scratch buffer", 8: "bad format", 10: "missing input", 11: "did not start", 12: "driver out of date", 17: "denied"}.get(int(rc) & 0xFFFFF, "0x%08x" % int(rc))

def dll_rank(path, ngx_dir):
    if ngx_dir is not None and path.parent == ngx_dir:
        return 0
    parts = [p.lower() for p in path.parts]
    if "driverstore" in parts:
        return 1
    if "system32" in parts:
        return 2
    return 3

def project_id():
    return os.environ.get("KEEL_DLSS_PROJECT_ID") or PROJECT_ID

def _first_file(candidates):
    for path in candidates:
        if path is not None and Path(path).is_file():
            return Path(path)
    return None

def find_ngx():
    candidates = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Services\nvlddmkm\NGXCore") as key:
                folder, _ = winreg.QueryValueEx(key, "NGXPath")
            candidates += [Path(folder) / "_nvngx.dll", Path(folder) / "nvngx.dll"]
        except OSError:
            pass
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    candidates += [system / "_nvngx.dll", system / "nvngx.dll"]
    return _first_file(candidates)

def find_dll():
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    ngx = find_ngx()
    ngx_dir = ngx.parent if ngx is not None else None
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    found = []
    if ngx_dir is not None:
        found.append(ngx_dir / "nvngx_dlss.dll")
    store = system_root / "DriverStore" / "FileRepository"
    if store.is_dir():
        found += list(store.glob("*/nvngx_dlss.dll"))
        if not any(p.is_file() for p in found):
            found += list(store.glob("*/*/nvngx_dlss.dll"))
    found.append(system_root / "nvngx_dlss.dll")
    for key in ("ProgramFiles", "ProgramW6432", "ProgramData"):
        value = os.environ.get(key)
        if not value:
            continue
        root = Path(value)
        for tail in (r"NVIDIA Corporation\NVIDIA NGX\nvngx_dlss.dll", r"NVIDIA Corporation\NVIDIA App\nvngx_dlss.dll", r"NVIDIA\NGX\models\nvngx_dlss.dll"):
            found.append(root / tail)
    files = [p for p in found if p.is_file()]
    if not files:
        return None
    files.sort(key=lambda p: (dll_rank(p, ngx_dir), -p.stat().st_mtime))
    return files[0]

def unavailable_reason():
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
    def __init__(self, ngx, feature, project):
        import ctypes
        self._ctypes = ctypes
        self._kept = []
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(ngx.parent))
            if feature is not None:
                os.add_dll_directory(str(feature.parent))
        self.lib = ctypes.CDLL(str(ngx))
        self._bind()
        folder = Path(tempfile.gettempdir()) / "keel-ngx"
        folder.mkdir(parents=True, exist_ok=True)
        info = self._feature_info(feature)
        feature_info = None if info is None else ctypes.cast(ctypes.byref(info), ctypes.c_void_p)
        rc = self._call("init", lambda: self._init(project.encode(), ENGINE_CUSTOM, b"keel", str(folder), feature_info, API_VERSION))
        if ngx_failed(rc) and feature_info is not None:
            rc = self._call("init", lambda: self._init(project.encode(), ENGINE_CUSTOM, b"keel", str(folder), None, API_VERSION))
        if ngx_failed(rc):
            raise RuntimeError("DLSS init " + ngx_why(rc))
        self._bag = {}
        self.params = self._parameter_block()
        self.handle = ctypes.c_void_p()
        self._src = (0, 0)
        self._zeros = None
        self._depth = None
        self._feature_dll = feature
        self._scratch = None
        self.kind = "dlss"

    def _sym(self, *names):
        for name in names:
            try:
                return getattr(self.lib, name)
            except AttributeError:
                continue
        raise AttributeError(names[0])

    def _feature_info(self, feature):
        if feature is None:
            return None
        c = self._ctypes
        path = c.c_wchar_p(str(feature.parent))
        slots = (c.c_wchar_p * 1)(path)
        class PathList(c.Structure):
            _fields_ = [("Path", c.POINTER(c.c_wchar_p)), ("Length", c.c_uint)]
        class Logging(c.Structure):
            _fields_ = [("LoggingCallback", c.c_void_p), ("MinimumLoggingLevel", c.c_int), ("DisableOtherLoggingSinks", c.c_bool)]
        class Common(c.Structure):
            _fields_ = [("PathListInfo", PathList), ("InternalData", c.c_void_p), ("LoggingInfo", Logging)]
        info = Common()
        info.PathListInfo.Path = c.cast(slots, c.POINTER(c.c_wchar_p))
        info.PathListInfo.Length = 1
        self._kept += [path, slots, info]
        return info

    def _bind(self):
        c, lib = self._ctypes, self.lib
        void, u32, i32 = c.c_void_p, c.c_uint, c.c_int
        self._init = self._sym("NVSDK_NGX_CUDA_Init_with_ProjectID", "NVSDK_NGX_CUDA_Init_ProjectID")
        self._init.argtypes = [c.c_char_p, i32, c.c_char_p, c.c_wchar_p, void, i32]
        self._init.restype = u32
        lib.NVSDK_NGX_CUDA_CreateFeature.argtypes = [i32, void, c.POINTER(void)]
        lib.NVSDK_NGX_CUDA_CreateFeature.restype = u32
        self._eval = self._sym("NVSDK_NGX_CUDA_EvaluateFeature_C", "NVSDK_NGX_CUDA_EvaluateFeature")
        self._eval.argtypes = [void, void, void]
        self._eval.restype = u32

    def _call(self, where, fn):
        try:
            return fn()
        except OSError as exc:
            raise RuntimeError("DLSS %s %s" % (where, exc)) from exc

    def _remember(self, name, value):
        if not name:
            return
        key = name.decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else str(name)
        self._bag[key] = value

    def _recall(self, name):
        if not name:
            return None
        key = name.decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else str(name)
        return self._bag.get(key)

    def _parameter_block(self):
        c = self._ctypes
        miss = FAIL | 1

        def put_p(_this, name, value):
            self._remember(name, 0 if not value else int(value))

        def put_i(_this, name, value):
            self._remember(name, int(value))

        def put_u(_this, name, value):
            self._remember(name, int(value))

        def put_d(_this, name, value):
            self._remember(name, float(value))

        def put_f(_this, name, value):
            self._remember(name, float(value))

        def put_ull(_this, name, value):
            self._remember(name, int(value))

        def give_num(name, out, cast):
            if not out:
                return miss
            got = self._recall(name)
            if got is None:
                return miss
            out[0] = cast(got)
            return SUCCESS

        def give_p(name, out):
            if not out:
                return miss
            got = self._recall(name)
            if got is None:
                return miss
            out[0] = c.c_void_p(int(got) if got else 0)
            return SUCCESS

        def get_p(_this, name, out):
            return give_p(name, out)

        def get_i(_this, name, out):
            return give_num(name, out, int)

        def get_u(_this, name, out):
            return give_num(name, out, int)

        def get_d(_this, name, out):
            return give_num(name, out, float)

        def get_f(_this, name, out):
            return give_num(name, out, float)

        def get_ull(_this, name, out):
            return give_num(name, out, int)

        def reset(_this):
            self._bag.clear()

        kinds = (
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_void_p), put_p),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_void_p), put_p),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_void_p), put_p),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_int), put_i),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_uint), put_u),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_double), put_d),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_float), put_f),
            (c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_ulonglong), put_ull),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_void_p)), get_p),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_void_p)), get_p),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_void_p)), get_p),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_int)), get_i),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_uint)), get_u),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_double)), get_d),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_float)), get_f),
            (c.CFUNCTYPE(c.c_uint, c.c_void_p, c.c_char_p, c.POINTER(c.c_ulonglong)), get_ull),
            (c.CFUNCTYPE(None, c.c_void_p), reset),
        )
        self._cbs = [proto(fn) for proto, fn in kinds]
        table = (c.c_void_p * len(self._cbs))(*[c.cast(cb, c.c_void_p) for cb in self._cbs])

        class Block(c.Structure):
            _fields_ = [("vtbl", c.c_void_p)]

        block = Block(vtbl=c.cast(table, c.c_void_p))
        self._table = table
        self._block = block
        return c.c_void_p(c.addressof(block))

    def _set_ui(self, name, value):
        self._remember(name, int(value))

    def _set_i(self, name, value):
        self._remember(name, int(value))

    def _set_f(self, name, value):
        self._remember(name, float(value))

    def _set_ptr(self, name, value):
        raw = value.value if hasattr(value, "value") else value
        self._remember(name, 0 if not raw else int(raw))

    def _feature(self, w, h):
        if self._src == (w, h):
            return
        self._set_ui(b"Width", w)
        self._set_ui(b"Height", h)
        self._set_ui(b"OutWidth", UHD_W)
        self._set_ui(b"OutHeight", UHD_H)
        self._set_i(b"PerfQualityValue", PERFORMANCE)
        self._set_i(b"RTXValue", 0)
        self._set_i(b"DLSS.Feature.Create.Flags", FLAG_AUTO_EXPOSURE)
        self._set_i(b"DLSS.Hint.Render.Preset.Performance", PRESET_M)
        self._set_i(b"DLSS.Hint.Render.Preset.Balanced", PRESET_K)
        self._set_i(b"DLSS.Hint.Render.Preset.Quality", PRESET_K)
        self._set_i(b"DLSS.Hint.Render.Preset.UltraPerformance", PRESET_L)
        self._set_i(b"DLSS.Hint.Render.Preset.DLAA", PRESET_K)
        self._set_ui(b"DLSS.Render.Subrect.Dimensions.Width", w)
        self._set_ui(b"DLSS.Render.Subrect.Dimensions.Height", h)
        self._set_i(b"DLSS.Enable.Output.Subrects", 0)
        why = self._open_feature(FEATURE_SUPER_SAMPLING)
        if why is None:
            self.kind = "dlss"
        else:
            self._set_ui(b"Scale", 2)
            image = self._open_feature(FEATURE_IMAGE_SR)
            if image is not None:
                where = self._feature_dll.name if self._feature_dll else "no nvngx_dlss.dll"
                raise RuntimeError("DLSS %s %sx%s %s; image %s" % (why, w, h, where, image))
            self.kind = "image"
        self._src = (w, h)

    def _open_feature(self, feature):
        self._reserve_scratch(feature)
        rc = self._call("create", lambda: self.lib.NVSDK_NGX_CUDA_CreateFeature(feature, self.params, self._ctypes.byref(self.handle)))
        return ngx_why(rc) if ngx_failed(rc) else None

    def _reserve_scratch(self, feature):
        lib = self.lib
        try:
            size_of = lib.NVSDK_NGX_CUDA_GetScratchBufferSize
        except AttributeError:
            return
        c = self._ctypes
        size_of.argtypes = [c.c_int, c.c_void_p, c.POINTER(c.c_size_t)]
        size_of.restype = c.c_uint
        need = c.c_size_t(0)
        rc = self._call("scratch", lambda: size_of(feature, self.params, c.byref(need)))
        if ngx_failed(rc) or not need.value:
            return
        import torch
        if self._scratch is None or self._scratch.numel() < need.value:
            self._scratch = torch.empty(need.value, dtype=torch.uint8, device="cuda")
        self._set_ptr(b"Scratch", c.c_void_p(self._scratch.data_ptr()))
        self._set_ui(b"Scratch.SizeInBytes", int(need.value))

    def upscale(self, bgr):
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
        if self._depth is None or self._depth.shape[0] != h * w:
            self._depth = torch.ones(h * w, dtype=torch.float32, device="cuda")
        c = self._ctypes
        self._set_ptr(b"Color", c.c_void_p(rgb.data_ptr()))
        self._set_ui(b"Color.Format", RGB8UI)
        self._set_ui(b"Color.SizeInBytes", w * h * 3)
        self._set_ptr(b"Output", c.c_void_p(out.data_ptr()))
        self._set_ui(b"Output.Format", RGB8UI)
        self._set_ui(b"Output.SizeInBytes", UHD_W * UHD_H * 3)
        if self.kind == "dlss":
            self._set_ptr(b"MotionVectors", c.c_void_p(self._zeros.data_ptr()))
            self._set_ptr(b"Depth", c.c_void_p(self._depth.data_ptr()))
            self._set_f(b"Jitter.Offset.X", 0.0)
            self._set_f(b"Jitter.Offset.Y", 0.0)
            self._set_f(b"MV.Scale.X", 1.0)
            self._set_f(b"MV.Scale.Y", 1.0)
            self._set_f(b"DLSS.Pre.Exposure", 1.0)
            self._set_f(b"DLSS.Exposure.Scale", 1.0)
            self._set_i(b"Reset", 1)
        rc = self._call("eval", lambda: self._eval(self.handle, self.params, None))
        if ngx_failed(rc):
            raise RuntimeError("DLSS evaluate " + ngx_why(rc))
        return out

_session = None

def session():
    global _session
    if _session is not None:
        return _session
    why = unavailable_reason()
    if why:
        _session = (None, why)
        return _session
    try:
        import torch
        torch.cuda.init()
        torch.empty(1, device="cuda")
        scaler = DlssScaler(find_ngx(), find_dll(), project_id())
    except (AttributeError, OSError, RuntimeError) as e:
        _session = (None, str(e)[:200])
        return _session
    _session = (scaler, None)
    return _session

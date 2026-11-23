"""Put the DLSS init call in the order the driver export reads.

NVSDK_NGX_CUDA_Init_ProjectID takes the SDK version and then the feature-info pointer.
The public header lists those two the other way around, and that order makes the driver
read address 0x15. Safe to run twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


_OLD_FIND = '''def find_dll():
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    ngx = find_ngx()
    ngx_dir = ngx.parent if ngx is not None else None
    system_root = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32"
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
        for tail in (r"NVIDIA Corporation\\NVIDIA NGX\\nvngx_dlss.dll", r"NVIDIA Corporation\\NVIDIA App\\nvngx_dlss.dll", r"NVIDIA\\NGX\\models\\nvngx_dlss.dll"):
            found.append(root / tail)
    files = [p for p in found if p.is_file()]
    if not files:
        return None
    files.sort(key=lambda p: (dll_rank(p, ngx_dir), -p.stat().st_mtime))
    return files[0]
'''

_NEW_FIND = '''def _is_pe(path):
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False

def _model_version(path):
    parts = path.parts
    if "versions" not in parts:
        return 0
    index = parts.index("versions") + 1
    if index < len(parts) and parts[index].isdigit():
        return int(parts[index])
    return 0

def pick_model(blobs):
    real = [path for path in blobs if path.is_file() and _is_pe(path)]
    if not real:
        return None
    return max(real, key=lambda path: (_model_version(path), path.stat().st_mtime))

def stage_dll(src, dest_dir):
    if src.suffix.lower() == ".dll":
        return src
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "nvngx_dlss.dll"
    if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, dest)
    return dest

def _model_bins():
    root = Path(os.environ.get("ProgramData", r"C:\\ProgramData")) / "NVIDIA" / "NGX" / "models" / "dlss" / "versions"
    if not root.is_dir():
        return []
    return [path for path in root.glob("*/files/*.bin") if path.is_file()]

def find_dll():
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    ngx = find_ngx()
    ngx_dir = ngx.parent if ngx is not None else None
    system_root = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32"
    found = []
    if ngx_dir is not None:
        found.append(ngx_dir / "nvngx_dlss.dll")
    store = system_root / "DriverStore" / "FileRepository"
    if store.is_dir():
        found += list(store.glob("*/nvngx_dlss.dll"))
        found += list(store.glob("*/*/nvngx_dlss.dll"))
    found.append(system_root / "nvngx_dlss.dll")
    models = Path(os.environ.get("ProgramData", r"C:\\ProgramData")) / "NVIDIA" / "NGX" / "models"
    if models.is_dir():
        found += list(models.rglob("nvngx_dlss.dll"))
    for key in ("ProgramFiles", "ProgramW6432", "ProgramData"):
        value = os.environ.get(key)
        if not value:
            continue
        root = Path(value)
        for tail in (r"NVIDIA Corporation\\NVIDIA NGX\\nvngx_dlss.dll", r"NVIDIA Corporation\\NVIDIA App\\nvngx_dlss.dll", r"NVIDIA\\NGX\\models\\nvngx_dlss.dll"):
            found.append(root / tail)
    files = [p for p in found if p.is_file()]
    files.sort(key=lambda p: (dll_rank(p, ngx_dir), -p.stat().st_mtime))
    if files and dll_rank(files[0], ngx_dir) <= 2:
        return files[0]
    model = pick_model(_model_bins())
    if model is not None:
        return stage_dll(model, Path(tempfile.gettempdir()) / "keel-ngx")
    return files[0] if files else None
'''


def _patch_model(text: str) -> str:
    """Current drivers store nvngx_dlss.dll as a .bin under ProgramData\\NVIDIA\\NGX\\models."""
    if "def pick_model(" in text:
        return text
    if "import shutil\n" not in text:
        text = text.replace("import os\n", "import os\nimport shutil\n", 1)
    if _OLD_FIND not in text:
        raise SystemExit("DLSS init patch could not find the DLL search")
    text = text.replace(_OLD_FIND, _NEW_FIND, 1)
    needle = "                os.add_dll_directory(str(feature.parent))\n"
    insert = needle + (
        "        if sys.platform == \"win32\" and feature is not None:\n"
        "            kernel = ctypes.WinDLL(\"kernel32\", use_last_error=True)\n"
        "            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]\n"
        "            kernel.SetDllDirectoryW(str(feature.parent))\n"
    )
    if "SetDllDirectoryW" not in text and needle in text:
        text = text.replace(needle, insert, 1)
    return text


def patch(text: str) -> str:
    text = text.replace(
        'self._sym("NVSDK_NGX_CUDA_Init_with_ProjectID", "NVSDK_NGX_CUDA_Init_ProjectID")',
        'self._sym("NVSDK_NGX_CUDA_Init_ProjectID", "NVSDK_NGX_CUDA_Init_with_ProjectID")',
    )
    text = text.replace(
        "[c.c_char_p, i32, c.c_char_p, c.c_wchar_p, void, i32]",
        "[c.c_char_p, i32, c.c_char_p, c.c_wchar_p, i32, void]",
    )
    text = text.replace(
        "str(folder), feature_info, API_VERSION)",
        "str(folder), API_VERSION, feature_info)",
    )
    text = text.replace(
        "str(folder), None, API_VERSION)",
        "str(folder), API_VERSION, None)",
    )
    if "_hold_cuda_context" not in text:
        text = text.replace(
            "        info = self._feature_info(feature)\n",
            "        self._hold_cuda_context()\n        info = self._feature_info(feature)\n",
            1,
        )
        hold = (
            "    def _hold_cuda_context(self):\n"
            '        if sys.platform != "win32":\n'
            "            return\n"
            "        c = self._ctypes\n"
            "        try:\n"
            '            nvcuda = c.CDLL("nvcuda.dll")\n'
            "        except OSError:\n"
            "            return\n"
            "        nvcuda.cuInit.argtypes = [c.c_uint]\n"
            "        nvcuda.cuInit.restype = c.c_int\n"
            "        nvcuda.cuInit(0)\n"
            "        nvcuda.cuCtxGetCurrent.argtypes = [c.POINTER(c.c_void_p)]\n"
            "        nvcuda.cuCtxGetCurrent.restype = c.c_int\n"
            "        current = c.c_void_p()\n"
            "        if nvcuda.cuCtxGetCurrent(c.byref(current)) != 0 or current.value:\n"
            "            return\n"
            "        nvcuda.cuDevicePrimaryCtxRetain.argtypes = [c.POINTER(c.c_void_p), c.c_int]\n"
            "        nvcuda.cuDevicePrimaryCtxRetain.restype = c.c_int\n"
            "        nvcuda.cuCtxSetCurrent.argtypes = [c.c_void_p]\n"
            "        nvcuda.cuCtxSetCurrent.restype = c.c_int\n"
            "        held = c.c_void_p()\n"
            "        if nvcuda.cuDevicePrimaryCtxRetain(c.byref(held), 0) == 0 and held.value:\n"
            "            nvcuda.cuCtxSetCurrent(held)\n"
            "            self._cuda_ctx = held\n\n"
        )
        text = text.replace("    def _call(self, where, fn):", hold + "    def _call(self, where, fn):", 1)
    text = _patch_model(text)
    text = _patch_eval(text)
    if "API_VERSION, feature_info" not in text and "GENERIC_APP" not in text:
        raise SystemExit("DLSS init patch lost the init call")
    if "i32, void]" not in text and "GENERIC_APP" not in text:
        raise SystemExit("DLSS init patch did not put the SDK version before the feature pointer")
    if "def _hold_cuda_context" not in text:
        raise SystemExit("DLSS init patch could not add the CUDA context hold")
    if "def pick_model(" not in text or "def _model_bins(" not in text:
        raise SystemExit("DLSS init patch did not look in the driver model folder")
    if "_adopt_driver_parameters" not in text or "_cuda_now" not in text or "_progress_cb" not in text:
        raise SystemExit("DLSS init patch did not give evaluate the driver's parameter object")
    return text


def _patch_eval(text: str) -> str:
    """The model file is registered to application id 0xE658700. A custom project id evaluates a null network."""
    if "_adopt_driver_parameters" in text and "_cuda_now" in text:
        return text
    text = text.replace(
        'self._sym("NVSDK_NGX_CUDA_EvaluateFeature_C", "NVSDK_NGX_CUDA_EvaluateFeature")',
        'self._sym("NVSDK_NGX_CUDA_EvaluateFeature", "NVSDK_NGX_CUDA_EvaluateFeature_C")',
    )
    old_init = (
        'rc = self._call("init", lambda: self._init(project.encode(), ENGINE_CUSTOM, b"keel", str(folder), API_VERSION, feature_info))\n'
        '        if ngx_failed(rc) and feature_info is not None:\n'
        '            rc = self._call("init", lambda: self._init(project.encode(), ENGINE_CUSTOM, b"keel", str(folder), API_VERSION, None))'
    )
    new_init = 'rc = self._start_runtime(project, str(folder), feature_info)'
    if old_init not in text:
        raise SystemExit("DLSS init patch could not find the init call")
    text = text.replace(old_init, new_init, 1)
    old_ctx = (
        "        if nvcuda.cuCtxGetCurrent(c.byref(current)) != 0 or current.value:\n"
        "            return\n"
    )
    new_ctx = (
        "        self._nvcuda = nvcuda\n"
        "        if nvcuda.cuCtxGetCurrent(c.byref(current)) == 0 and current.value:\n"
        "            self._cuda_ctx = c.c_void_p(current.value)\n"
        "            return\n"
    )
    if old_ctx not in text:
        raise SystemExit("DLSS init patch could not find the CUDA context check")
    text = text.replace(old_ctx, new_ctx, 1)
    old_eval = 'rc = self._call("eval", lambda: self._eval(self.handle, self.params, None))'
    new_eval = (
        'if not self.handle.value:\n'
        '            raise RuntimeError("DLSS eval no handle")\n'
        '        self._cuda_now()\n'
        '        rc = self._call("eval", lambda: self._eval(self.handle, self._live(), self._progress_cb()))'
    )
    if old_eval not in text:
        raise SystemExit("DLSS init patch could not find evaluate")
    text = text.replace(old_eval, new_eval, 1)
    old_sets = (
        "    def _set_ui(self, name, value):\n"
        "        self._remember(name, int(value))\n\n"
        "    def _set_i(self, name, value):\n"
        "        self._remember(name, int(value))\n\n"
        "    def _set_f(self, name, value):\n"
        "        self._remember(name, float(value))\n\n"
        "    def _set_ptr(self, name, value):\n"
        "        raw = value.value if hasattr(value, \"value\") else value\n"
        "        self._remember(name, 0 if not raw else int(raw))\n"
    )
    new_sets = (
        "    def _set_ui(self, name, value):\n"
        "        self._remember(name, int(value))\n"
        "        self._mirror(4, name, int(value), self._ctypes.c_uint)\n\n"
        "    def _set_i(self, name, value):\n"
        "        self._remember(name, int(value))\n"
        "        self._mirror(3, name, int(value), self._ctypes.c_int)\n\n"
        "    def _set_f(self, name, value):\n"
        "        self._remember(name, float(value))\n"
        "        self._mirror(6, name, float(value), self._ctypes.c_float)\n\n"
        "    def _set_ptr(self, name, value):\n"
        "        raw = value.value if hasattr(value, \"value\") else value\n"
        "        kept = 0 if not raw else int(raw)\n"
        "        self._remember(name, kept)\n"
        "        self._mirror(0, name, self._ctypes.c_void_p(kept), self._ctypes.c_void_p)\n"
    )
    if old_sets not in text:
        raise SystemExit("DLSS init patch could not find the parameter setters")
    text = text.replace(old_sets, new_sets, 1)
    text = text.replace(
        "NVSDK_NGX_CUDA_CreateFeature(feature, self.params, self._ctypes.byref(self.handle))",
        "NVSDK_NGX_CUDA_CreateFeature(feature, self._live(), self._ctypes.byref(self.handle))",
        1,
    )
    helpers = '''
    def _cuda_now(self):
        ctx = getattr(self, "_cuda_ctx", None)
        nvcuda = getattr(self, "_nvcuda", None)
        if ctx is None or nvcuda is None or not getattr(ctx, "value", None):
            return
        c = self._ctypes
        nvcuda.cuCtxSetCurrent.argtypes = [c.c_void_p]
        nvcuda.cuCtxSetCurrent.restype = c.c_int
        nvcuda.cuCtxSetCurrent(ctx)

    def _progress_cb(self):
        made = getattr(self, "_progress", None)
        if made is not None:
            return made
        c = self._ctypes

        def _keep(_amount, cancel):
            if cancel:
                cancel[0] = False

        self._progress = c.CFUNCTYPE(None, c.c_float, c.POINTER(c.c_bool))(_keep)
        return self._progress

    def _start_runtime(self, project, folder, feature_info):
        try:
            rc = self._init(project.encode(), ENGINE_CUSTOM, b"keel", folder, API_VERSION, feature_info)
        except OSError:
            rc = 0
        if ngx_failed(rc) and feature_info is not None:
            try:
                rc = self._init(project.encode(), ENGINE_CUSTOM, b"keel", folder, API_VERSION, None)
            except OSError:
                rc = 0
        self._adopt_driver_parameters()
        return rc

    def _adopt_driver_parameters(self):
        self._driver_params = None
        c = self._ctypes
        for name in ("NVSDK_NGX_CUDA_GetCapabilityParameters", "NVSDK_NGX_CUDA_GetParameters"):
            try:
                fn = getattr(self.lib, name)
            except AttributeError:
                continue
            fn.argtypes = [c.POINTER(c.c_void_p)]
            fn.restype = c.c_uint
            params = c.c_void_p()
            try:
                rc = fn(c.byref(params))
            except OSError:
                continue
            if not ngx_failed(rc) and params.value:
                self._driver_params = params
                return

    def _mirror(self, slot, name, value, typ):
        drv = getattr(self, "_driver_params", None)
        if drv is None or not getattr(drv, "value", None):
            return
        c = self._ctypes
        try:
            vtable = c.cast(drv, c.POINTER(c.c_void_p))[0]
            if not vtable:
                raise OSError("empty")
            table = c.cast(vtable, c.POINTER(c.c_void_p))
            c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, typ)(table[slot])(drv, name, value)
        except OSError:
            self._driver_params = None

    def _live(self):
        drv = getattr(self, "_driver_params", None)
        if drv is not None and getattr(drv, "value", None):
            return drv
        return self.params

'''
    anchor = "    def _call(self, where, fn):"
    if anchor not in text:
        raise SystemExit("DLSS init patch could not find the call wrapper")
    text = text.replace(anchor, helpers + anchor, 1)
    return text


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS init uses the driver argument order")


if __name__ == "__main__":
    main()

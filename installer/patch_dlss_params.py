"""Use the driver's parameter object, and give it the CUDA context. Run after the texture patch.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HELPERS = '''
    def _cuda_now(self):
        c = self._ctypes
        nvcuda = getattr(self, "_nvcuda", None)
        if nvcuda is None:
            try:
                nvcuda = c.CDLL("nvcuda.dll")
            except OSError:
                return 0
            self._nvcuda = nvcuda
            nvcuda.cuInit.argtypes = [c.c_uint]
            nvcuda.cuInit.restype = c.c_int
            nvcuda.cuInit(0)
        nvcuda.cuCtxGetCurrent.argtypes = [c.POINTER(c.c_void_p)]
        nvcuda.cuCtxGetCurrent.restype = c.c_int
        nvcuda.cuDevicePrimaryCtxRetain.argtypes = [c.POINTER(c.c_void_p), c.c_int]
        nvcuda.cuDevicePrimaryCtxRetain.restype = c.c_int
        nvcuda.cuCtxSetCurrent.argtypes = [c.c_void_p]
        nvcuda.cuCtxSetCurrent.restype = c.c_int
        current = c.c_void_p()
        if nvcuda.cuCtxGetCurrent(c.byref(current)) != 0 or not current.value:
            held = c.c_void_p()
            if nvcuda.cuDevicePrimaryCtxRetain(c.byref(held), 0) != 0 or not held.value:
                return 0
            nvcuda.cuCtxSetCurrent(held)
            current = held
        self._cuda_ctx = c.c_void_p(current.value)
        nvcuda.cuCtxSetCurrent(self._cuda_ctx)
        return int(current.value)

    def _bind_cuda_context(self):
        ctx = self._cuda_now()
        if not ctx:
            raise RuntimeError("DLSS ctx")
        self._set_ptr(b"Input1", self._ctypes.c_void_p(ctx))

    def _adopt_driver_parameters(self):
        c = self._ctypes
        self._driver_params = None
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
        driver = getattr(self, "_driver_params", None)
        if driver is None or not getattr(driver, "value", None):
            return
        c = self._ctypes
        try:
            vtable = c.cast(driver, c.POINTER(c.c_void_p))[0]
            if not vtable:
                raise OSError("empty")
            table = c.cast(vtable, c.POINTER(c.c_void_p))
            c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, typ)(table[slot])(driver, name, value)
        except OSError as exc:
            raise RuntimeError("DLSS set %s" % exc) from exc

    def _live(self):
        driver = getattr(self, "_driver_params", None)
        if driver is not None and getattr(driver, "value", None):
            return driver
        return self.params

'''

SETTERS = '''    def _set_ui(self, name, value):
        self._remember(name, int(value))
        self._mirror(4, name, int(value), self._ctypes.c_uint)

    def _set_i(self, name, value):
        self._remember(name, int(value))
        self._mirror(3, name, int(value), self._ctypes.c_int)

    def _set_f(self, name, value):
        self._remember(name, float(value))
        self._mirror(6, name, float(value), self._ctypes.c_float)

    def _set_ptr(self, name, value):
        raw = value.value if hasattr(value, "value") else value
        kept = 0 if not raw else int(raw)
        self._remember(name, kept)
        self._mirror(0, name, self._ctypes.c_void_p(kept), self._ctypes.c_void_p)
'''

OLD_SETTERS = '''    def _set_ui(self, name, value):
        self._remember(name, int(value))

    def _set_i(self, name, value):
        self._remember(name, int(value))

    def _set_f(self, name, value):
        self._remember(name, float(value))

    def _set_ptr(self, name, value):
        raw = value.value if hasattr(value, "value") else value
        self._remember(name, 0 if not raw else int(raw))
'''


def patch(text: str) -> str:
    if "self._adopt_driver_parameters()" not in text:
        text = text.replace(
            "self.params = self._parameter_block()\n",
            "self.params = self._parameter_block()\n        self._adopt_driver_parameters()\n",
            1,
        )
    if OLD_SETTERS in text:
        text = text.replace(OLD_SETTERS, SETTERS, 1)
    if "def _open_feature(self, feature):\n        self._bind_cuda_context()\n" not in text:
        text = text.replace(
            "def _open_feature(self, feature):\n        self._reserve_scratch(feature)\n",
            "def _open_feature(self, feature):\n        self._bind_cuda_context()\n        self._reserve_scratch(feature)\n",
            1,
        )
    text = text.replace(
        "NVSDK_NGX_CUDA_CreateFeature(feature, self.params,",
        "NVSDK_NGX_CUDA_CreateFeature(feature, self._live(),",
    )
    text = text.replace(
        "size_of(feature, self.params,",
        "size_of(feature, self._live(),",
    )
    old_eval = 'rc = self._call("eval", lambda: self._eval(self.handle, self.params, None))'
    new_eval = 'self._bind_cuda_context()\n        rc = self._call("eval", lambda: self._eval(self.handle, self._live(), None))'
    if old_eval in text:
        text = text.replace(old_eval, new_eval, 1)
    elif 'self._bind_cuda_context()\n        rc = self._call("eval"' not in text:
        text = text.replace(
            'rc = self._call("eval", lambda: self._eval(self.handle, self._live(), None))',
            new_eval,
            1,
        )
    if "def _adopt_driver_parameters(" not in text:
        text = text.replace("\n_session = None\n", HELPERS + "\n_session = None\n", 1)
    if "def _bind_cuda_context(" not in text or "self._live()" not in text:
        raise SystemExit("DLSS parameter patch did not apply")
    return text


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    shown = ROOT / "framegen" / "io.py"
    if shown.is_file():
        body = shown.read_text(encoding="utf-8")
        shown.write_text(body.replace("why[:42]", "why[:96]"), encoding="utf-8")
    print("DLSS uses the driver parameter object and the CUDA context")


if __name__ == "__main__":
    main()

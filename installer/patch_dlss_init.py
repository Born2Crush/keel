"""Put the DLSS init call in the order the driver export reads.

NVSDK_NGX_CUDA_Init_ProjectID takes the SDK version and then the feature-info pointer.
The public header lists those two the other way around, and that order makes the driver
read address 0x15. Safe to run twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    if "API_VERSION, feature_info" not in text or "i32, void]" not in text:
        raise SystemExit("DLSS init patch did not put the SDK version before the feature pointer")
    if "def _hold_cuda_context" not in text:
        raise SystemExit("DLSS init patch could not add the CUDA context hold")
    return text


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS init uses the driver argument order")


if __name__ == "__main__":
    main()

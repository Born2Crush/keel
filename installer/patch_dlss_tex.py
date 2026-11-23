"""Turn the DLSS picture pointers into CUDA textures. Run after patch_dlss_init.py. Safe twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

METHODS = '''
    def _texture(self, tensor, channels, fmt, read_as_integer):
        c = self._ctypes
        nvcuda = getattr(self, "_nvcuda", None)
        if nvcuda is None:
            try:
                nvcuda = c.CDLL("nvcuda.dll")
            except OSError:
                raise RuntimeError("DLSS tex no cuda")
            self._nvcuda = nvcuda
            nvcuda.cuInit.argtypes = [c.c_uint]
            nvcuda.cuInit.restype = c.c_int
            nvcuda.cuInit(0)
        class Pitch(c.Structure):
            _fields_ = [("devPtr", c.c_uint64), ("format", c.c_int), ("numChannels", c.c_uint), ("width", c.c_size_t), ("height", c.c_size_t), ("pitchInBytes", c.c_size_t)]
        class Res(c.Union):
            _fields_ = [("pitch2D", Pitch), ("reserved", c.c_int * 32)]
        class Desc(c.Structure):
            _fields_ = [("resType", c.c_int), ("res", Res), ("flags", c.c_uint)]
        class TexDesc(c.Structure):
            _fields_ = [("addressMode", c.c_int * 3), ("filterMode", c.c_int), ("flags", c.c_uint), ("maxAnisotropy", c.c_uint), ("mipmapFilterMode", c.c_int), ("mipmapLevelBias", c.c_float), ("minMipmapLevelClamp", c.c_float), ("maxMipmapLevelClamp", c.c_float), ("borderColor", c.c_float * 4), ("reserved", c.c_int * 12)]
        desc = Desc()
        desc.resType = 3
        desc.flags = 0
        desc.res.pitch2D.devPtr = int(tensor.data_ptr())
        desc.res.pitch2D.format = fmt
        desc.res.pitch2D.numChannels = channels
        desc.res.pitch2D.width = int(tensor.shape[1])
        desc.res.pitch2D.height = int(tensor.shape[0])
        desc.res.pitch2D.pitchInBytes = int(tensor.shape[1]) * channels * int(tensor.element_size())
        tex = TexDesc()
        tex.addressMode[0] = tex.addressMode[1] = tex.addressMode[2] = 1
        tex.filterMode = 0
        tex.flags = 1 if read_as_integer else 0
        made = c.c_ulonglong(0)
        create = nvcuda.cuTexObjectCreate
        create.argtypes = [c.POINTER(c.c_ulonglong), c.POINTER(Desc), c.POINTER(TexDesc), c.c_void_p]
        create.restype = c.c_int
        rc = create(c.byref(made), c.byref(desc), c.byref(tex), None)
        if rc != 0:
            raise RuntimeError("DLSS tex %s" % rc)
        if not hasattr(self, "_tex_alive"):
            self._tex_alive = []
        self._tex_alive.append(made)
        return made

    def _output_surface(self, width, height):
        c = self._ctypes
        nvcuda = getattr(self, "_nvcuda", None)
        if nvcuda is None:
            nvcuda = c.CDLL("nvcuda.dll")
            self._nvcuda = nvcuda
            nvcuda.cuInit.argtypes = [c.c_uint]
            nvcuda.cuInit.restype = c.c_int
            nvcuda.cuInit(0)
        class ArrayDesc(c.Structure):
            _fields_ = [("Width", c.c_size_t), ("Height", c.c_size_t), ("Depth", c.c_size_t), ("Format", c.c_int), ("NumChannels", c.c_uint), ("Flags", c.c_uint)]
        ad = ArrayDesc()
        ad.Width = width
        ad.Height = height
        ad.Depth = 0
        ad.Format = 1
        ad.NumChannels = 4
        ad.Flags = 2
        arr = c.c_void_p()
        make = nvcuda.cuArray3DCreate
        make.argtypes = [c.POINTER(c.c_void_p), c.POINTER(ArrayDesc)]
        make.restype = c.c_int
        rc = make(c.byref(arr), c.byref(ad))
        if rc != 0 or not arr.value:
            raise RuntimeError("DLSS array %s" % rc)
        class Arr(c.Structure):
            _fields_ = [("hArray", c.c_void_p)]
        class Res(c.Union):
            _fields_ = [("array", Arr), ("reserved", c.c_int * 32)]
        class Desc(c.Structure):
            _fields_ = [("resType", c.c_int), ("res", Res), ("flags", c.c_uint)]
        desc = Desc()
        desc.resType = 0
        desc.flags = 0
        desc.res.array.hArray = arr
        surf = c.c_ulonglong(0)
        create = nvcuda.cuSurfObjectCreate
        create.argtypes = [c.POINTER(c.c_ulonglong), c.POINTER(Desc)]
        create.restype = c.c_int
        rc = create(c.byref(surf), c.byref(desc))
        if rc != 0 or not surf.value:
            raise RuntimeError("DLSS surf %s" % rc)
        if not hasattr(self, "_tex_alive"):
            self._tex_alive = []
        self._tex_alive.append(arr)
        self._tex_alive.append(surf)
        self._out_array = arr
        return surf

    def _read_output(self):
        c = self._ctypes
        nvcuda = self._nvcuda
        class Copy(c.Structure):
            _fields_ = [("srcXInBytes", c.c_size_t), ("srcY", c.c_size_t), ("srcMemoryType", c.c_int), ("_pad0", c.c_int), ("srcHost", c.c_void_p), ("srcDevice", c.c_void_p), ("srcArray", c.c_void_p), ("srcPitch", c.c_size_t), ("dstXInBytes", c.c_size_t), ("dstY", c.c_size_t), ("dstMemoryType", c.c_int), ("_pad1", c.c_int), ("dstHost", c.c_void_p), ("dstDevice", c.c_void_p), ("dstArray", c.c_void_p), ("dstPitch", c.c_size_t), ("WidthInBytes", c.c_size_t), ("Height", c.c_size_t)]
        copy = Copy()
        copy.srcMemoryType = 3
        copy.srcArray = self._out_array
        copy.dstMemoryType = 2
        copy.dstDevice = int(self._out4.data_ptr())
        copy.dstPitch = 3840 * 4
        copy.WidthInBytes = 3840 * 4
        copy.Height = 2160
        move = nvcuda.cuMemcpy2D
        move.argtypes = [c.POINTER(Copy)]
        move.restype = c.c_int
        rc = move(c.byref(copy))
        if rc != 0:
            raise RuntimeError("DLSS copy %s" % rc)

    def _pictures(self, rgb, h, w):
        import torch
        missing = getattr(self, "_out_tex", None) is None or getattr(self, "_rgba", None) is None
        if not missing and (self._rgba.shape[0] != h or self._rgba.shape[1] != w):
            missing = True
        if missing:
            rgba = torch.empty((h, w, 4), dtype=torch.uint8, device="cuda")
            out4 = torch.empty((2160, 3840, 4), dtype=torch.uint8, device="cuda")
            zeros = torch.zeros((h, w, 2), dtype=torch.float32, device="cuda")
            depth = torch.ones((h, w, 1), dtype=torch.float32, device="cuda")
            self._tex_alive = []
            color = self._texture(rgba, 4, 1, True)
            out_tex = self._output_surface(3840, 2160)
            motion = self._texture(zeros, 2, 32, False)
            depth_tex = self._texture(depth, 1, 32, False)
            self._rgba, self._out4, self._zeros, self._depth = rgba, out4, zeros, depth
            self._color_tex, self._out_tex, self._motion_tex, self._depth_tex = color, out_tex, motion, depth_tex
        self._rgba[..., :3] = rgb
        self._rgba[..., 3] = 255
'''

OLD_BUFFERS = '''        out = torch.empty((UHD_H, UHD_W, 3), dtype=torch.uint8, device="cuda")
        if self._zeros is None or self._zeros.shape[0] != h * w * 2:
            self._zeros = torch.zeros(h * w * 2, dtype=torch.float32, device="cuda")
        if self._depth is None or self._depth.shape[0] != h * w:
            self._depth = torch.ones(h * w, dtype=torch.float32, device="cuda")
'''


def patch(text: str) -> str:
    old = 'self._set_ptr(b"Color", c.c_void_p(rgb.data_ptr()))'
    if old not in text and "_pictures(rgb, h, w)" not in text:
        raise SystemExit("DLSS texture patch could not find the picture pointers")
    text = text.replace(
        old,
        'self._pictures(rgb, h, w)\n        self._set_ptr(b"Color", c.c_void_p(c.addressof(self._color_tex)))',
        1,
    )
    text = text.replace('self._set_ui(b"Color.Format", RGB8UI)', 'self._set_ui(b"Color.Format", 4)', 1)
    text = text.replace('self._set_ui(b"Color.Format", 3)', 'self._set_ui(b"Color.Format", 4)', 1)
    text = text.replace('self._set_ui(b"Color.SizeInBytes", w * h * 3)', 'self._set_ui(b"Color.SizeInBytes", w * h * 4)', 1)
    text = text.replace('self._set_ptr(b"Output", c.c_void_p(out.data_ptr()))', 'self._set_ptr(b"Output", c.c_void_p(c.addressof(self._out_tex)))', 1)
    text = text.replace('self._set_ui(b"Output.Format", RGB8UI)', 'self._set_ui(b"Output.Format", 4)', 1)
    text = text.replace('self._set_ui(b"Output.Format", 3)', 'self._set_ui(b"Output.Format", 4)', 1)
    text = text.replace('self._set_ui(b"Output.SizeInBytes", UHD_W * UHD_H * 3)', 'self._set_ui(b"Output.SizeInBytes", UHD_W * UHD_H * 4)', 1)
    text = text.replace('self._set_ptr(b"MotionVectors", c.c_void_p(self._zeros.data_ptr()))', 'self._set_ptr(b"MotionVectors", c.c_void_p(c.addressof(self._motion_tex)))', 1)
    text = text.replace('self._set_ptr(b"Depth", c.c_void_p(self._depth.data_ptr()))', 'self._set_ptr(b"Depth", c.c_void_p(c.addressof(self._depth_tex)))', 1)
    text = text.replace(
        "        return out\n",
        "        self._read_output()\n        return self._out4[..., :3].contiguous()\n",
        1,
    )
    if OLD_BUFFERS in text:
        text = text.replace(OLD_BUFFERS, "", 1)
    if "def _pictures(self, rgb, h, w):" not in text:
        text = text.replace("\n_session = None\n", METHODS + "\n_session = None\n", 1)
    if "cuSurfObjectCreate" not in text or "cuArray3DCreate" not in text or 'b"Color.Format", 4' not in text:
        raise SystemExit("DLSS texture patch did not apply")
    return text


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS frame call uses CUDA textures")


if __name__ == "__main__":
    main()

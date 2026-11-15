"""Window presentation through a DXGI flip-model swap chain: settings "present" = "dxgi" (Experimental).

Why: OpenGL's own SwapBuffers in a window goes through the Windows compositor as a copy. PresentMon on an RTX 4070
Ti SUPER (scripts/presentmon_check.ps1) showed "Composed: Copy with GPU GDI": about 12 ms more until a frame is on
screen than fullscreen, 3-4% of frames never shown, and frames snapped to the display's refresh, so no G-Sync or
FreeSync in a window. A flip-model swap chain (what games use) lets Windows hand frames to the display directly
("Independent Flip"), in a window too.

How: WGL_NV_DX_interop2 shares the swap chain's back buffer with OpenGL as a renderbuffer. The window still draws
everything with OpenGL, into an offscreen framebuffer (fbo); present() copies that into the back buffer, flipped
(Direct3D's rows run top-down, OpenGL's bottom-up), and calls IDXGISwapChain::Present. The visible window has no
OpenGL context of its own (GLFW NO_API), so OpenGL and DXGI never both present to it; the caller keeps OpenGL on a
hidden window.

NVIDIA only (the interop extension), with Direct3D 11 on the NVIDIA adapter OpenGL runs on. Any failure while
setting up raises, and the caller falls back to OpenGL's own presentation. COM is called through ctypes; the vtable
slots below are from the Windows SDK headers (dxgi.h, dxgi1_2.h, dxgi1_5.h, d3d11.h).

Testing: FRAMEGEN_PRESENT_SNAP=<folder> saves the back buffer as Direct3D is about to show it (read back, so it's
what the window gets, upright or not) every SNAP_EVERY presents and after each resize (scripts/dxgi_smoke.py).

Under test (the fullscreen stall: going fullscreen could block for 2 s or more):
  FRAMEGEN_DXGI_MODE=direct  OpenGL draws straight into the swap chain's back buffer (shared with the interop).
                     copy    OpenGL draws into a texture of our own (shared instead), and Direct3D copies that into
                             the back buffer before Present. The interop then never holds a swap chain buffer, which
                             Windows may be scanning out in fullscreen (Independent Flip). One extra GPU copy.
  FRAMEGEN_DXGI_BUFFERS=2|3  the swap chain's buffer count.
A lock or Present over 250 ms is logged separately, to show which one waits.
"""
from __future__ import annotations

import ctypes
import os
import time
import uuid
from ctypes import POINTER, WINFUNCTYPE, byref, c_char_p, c_int, c_long, c_size_t, c_uint, c_ulong, c_void_p

# ---------------------------------------------------------------------------------------------- Windows constants
DXGI_FORMAT_UNKNOWN = 0
DXGI_FORMAT_B8G8R8A8_UNORM = 87
DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
DXGI_SCALING_STRETCH = 0
DXGI_SWAP_EFFECT_FLIP_DISCARD = 4
DXGI_ALPHA_MODE_IGNORE = 3
DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING = 2048
DXGI_PRESENT_ALLOW_TEARING = 0x200
DXGI_FEATURE_PRESENT_ALLOW_TEARING = 0
DXGI_MWA_NO_ALT_ENTER = 2
DXGI_ERROR_NOT_FOUND = ctypes.c_long(0x887A0002).value
D3D_DRIVER_TYPE_UNKNOWN = 0  # an adapter is given
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_SDK_VERSION = 7
NVIDIA = 0x10DE
WGL_ACCESS_WRITE_DISCARD_NV = 0x0002
GL_RENDERBUFFER = 0x8D41
D3D11_USAGE_DEFAULT = 0
D3D11_USAGE_STAGING = 3
D3D11_BIND_SHADER_RESOURCE = 0x8
D3D11_BIND_RENDER_TARGET = 0x20
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1
SNAP_DIR = os.environ.get("FRAMEGEN_PRESENT_SNAP")
SNAP_EVERY, SNAP_MAX = 90, 80
MODE = os.environ.get("FRAMEGEN_DXGI_MODE", "direct")  # see the module docstring
BUFFERS = min(3, max(2, int(os.environ.get("FRAMEGEN_DXGI_BUFFERS", "2") or 2)))
SLOW_MS = 250.0

IID_IDXGIFactory1 = "770aae78-f26f-4dba-a829-253c83d1b387"
IID_IDXGIFactory2 = "50c83a1c-e072-4c48-87b0-3630fa36a6d0"
IID_IDXGIFactory5 = "7632e1f5-ee65-4dca-87fd-84cd75f8838d"
IID_IDXGIDevice1 = "77db970f-6276-48ba-ba28-070143b4392c"
IID_ID3D11Texture2D = "6f15aaf2-d208-4e89-9ab4-489535d34f9c"

# vtable slots (0-2 are IUnknown's QueryInterface, AddRef, Release)
QUERY_INTERFACE, RELEASE = 0, 2
FACTORY_ENUM_ADAPTERS1, FACTORY_MAKE_WINDOW_ASSOCIATION = 12, 8
FACTORY2_CREATE_SWAP_CHAIN_FOR_HWND = 15
FACTORY5_CHECK_FEATURE_SUPPORT = 28
ADAPTER1_GET_DESC1 = 10
DEVICE1_SET_MAXIMUM_FRAME_LATENCY = 12
SWAP_CHAIN_PRESENT, SWAP_CHAIN_GET_BUFFER, SWAP_CHAIN_RESIZE_BUFFERS = 8, 9, 13
DEVICE_CREATE_TEXTURE2D = 5
CONTEXT_MAP, CONTEXT_UNMAP, CONTEXT_COPY_RESOURCE = 14, 15, 47


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class SwapChainDesc1(ctypes.Structure):  # DXGI_SWAP_CHAIN_DESC1
    _fields_ = [("Width", c_uint), ("Height", c_uint), ("Format", c_uint), ("Stereo", c_int),
                ("SampleCount", c_uint), ("SampleQuality", c_uint), ("BufferUsage", c_uint), ("BufferCount", c_uint),
                ("Scaling", c_uint), ("SwapEffect", c_uint), ("AlphaMode", c_uint), ("Flags", c_uint)]


class Texture2DDesc(ctypes.Structure):  # D3D11_TEXTURE2D_DESC
    _fields_ = [("Width", c_uint), ("Height", c_uint), ("MipLevels", c_uint), ("ArraySize", c_uint), ("Format", c_uint),
                ("SampleCount", c_uint), ("SampleQuality", c_uint), ("Usage", c_uint), ("BindFlags", c_uint),
                ("CPUAccessFlags", c_uint), ("MiscFlags", c_uint)]


class MappedSubresource(ctypes.Structure):  # D3D11_MAPPED_SUBRESOURCE
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


class Luid(ctypes.Structure):
    _fields_ = [("LowPart", c_uint), ("HighPart", c_long)]


class AdapterDesc1(ctypes.Structure):  # DXGI_ADAPTER_DESC1
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                ("SubSysId", c_uint), ("Revision", c_uint), ("DedicatedVideoMemory", c_size_t),
                ("DedicatedSystemMemory", c_size_t), ("SharedSystemMemory", c_size_t), ("AdapterLuid", Luid),
                ("Flags", c_uint)]


def _com(obj: c_void_p, slot: int, argtypes: list, *args, restype=c_long):
    """Calls method `slot` of a COM object. HRESULTs come back as numbers (negative: failed)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])(obj, *args)


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def _release(obj: c_void_p | None) -> None:
    if obj:
        _com(obj, RELEASE, [], restype=c_ulong)


def _query(obj: c_void_p, iid: str) -> c_void_p | None:
    out = c_void_p()
    return out if _com(obj, QUERY_INTERFACE, [POINTER(GUID), POINTER(c_void_p)], byref(GUID.of(iid)),
                       byref(out)) >= 0 else None


class _Wgl:
    """The WGL_NV_DX_interop2 functions, from the current OpenGL context."""

    def __init__(self) -> None:
        gl = ctypes.WinDLL("opengl32")
        gl.wglGetProcAddress.restype, gl.wglGetProcAddress.argtypes = c_void_p, [c_char_p]
        gl.wglGetCurrentDC.restype = c_void_p

        def fn(name: str, restype, *argtypes):
            p = gl.wglGetProcAddress(name.encode())
            if not p:
                raise OSError(f"{name} isn't available")
            return WINFUNCTYPE(restype, *argtypes)(p)

        ext = fn("wglGetExtensionsStringARB", c_char_p, c_void_p)(gl.wglGetCurrentDC()) or b""
        if b"WGL_NV_DX_interop2" not in ext.split():
            raise OSError("the graphics driver doesn't offer WGL_NV_DX_interop2")
        HANDLE = c_void_p
        self.open_device = fn("wglDXOpenDeviceNV", HANDLE, c_void_p)
        self.close_device = fn("wglDXCloseDeviceNV", c_int, HANDLE)
        self.register = fn("wglDXRegisterObjectNV", HANDLE, HANDLE, c_void_p, c_uint, c_uint, c_uint)
        self.unregister = fn("wglDXUnregisterObjectNV", c_int, HANDLE, HANDLE)
        self.lock = fn("wglDXLockObjectsNV", c_int, HANDLE, c_int, POINTER(HANDLE))
        self.unlock = fn("wglDXUnlockObjectsNV", c_int, HANDLE, c_int, POINTER(HANDLE))


def _nvidia_adapter(factory: c_void_p) -> c_void_p:
    """The first NVIDIA adapter: the interop works only with a Direct3D device on the GPU OpenGL runs on (on a
    laptop the default adapter is often the integrated one)."""
    i = 0
    while True:
        adapter = c_void_p()
        hr = _com(factory, FACTORY_ENUM_ADAPTERS1, [c_uint, POINTER(c_void_p)], i, byref(adapter))
        if hr == DXGI_ERROR_NOT_FOUND:
            raise OSError("no NVIDIA graphics card found by DXGI")
        _check(hr, "EnumAdapters1")
        desc = AdapterDesc1()
        if _com(adapter, ADAPTER1_GET_DESC1, [POINTER(AdapterDesc1)], byref(desc)) >= 0 and desc.VendorId == NVIDIA:
            return adapter
        _release(adapter)
        i += 1


class DxgiPresenter:
    """A flip-model swap chain on `hwnd`, fed by OpenGL. Draw into `fbo` (sized with resize()), then present().

    Needs the OpenGL context that will draw to be current. Raises OSError (or RuntimeError) if anything is missing.
    """

    def __init__(self, GL, hwnd: int, w: int, h: int) -> None:
        self.GL, self.hwnd = GL, hwnd
        self.w = self.h = 0
        self.factory = self.factory2 = self.adapter = self.device = self.context = self.swap_chain = None
        self.back = self.shared = self.obj = self.hdev = None
        self.tex = self.fbo = self.rb = self.back_fbo = None
        self.presents, self.snaps, self.snap_next = 0, 0, False
        self.mode = MODE if MODE in ("direct", "copy") else "direct"
        try:
            self._open(w, h)
        except BaseException:
            self.close()
            raise

    def _open(self, w: int, h: int) -> None:
        GL = self.GL
        self.wgl = _Wgl()
        dxgi, d3d11 = ctypes.WinDLL("dxgi"), ctypes.WinDLL("d3d11")
        self.factory = c_void_p()
        _check(dxgi.CreateDXGIFactory1(byref(GUID.of(IID_IDXGIFactory1)), byref(self.factory)), "CreateDXGIFactory1")
        self.adapter = _nvidia_adapter(self.factory)
        self.device, self.context, level = c_void_p(), c_void_p(), c_uint()
        d3d11.D3D11CreateDevice.argtypes = [c_void_p, c_uint, c_void_p, c_uint, c_void_p, c_uint, c_uint,
                                            POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p)]
        _check(d3d11.D3D11CreateDevice(self.adapter, D3D_DRIVER_TYPE_UNKNOWN, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                       None, 0, D3D11_SDK_VERSION, byref(self.device), byref(level),
                                       byref(self.context)), "D3D11CreateDevice")
        self.factory2 = _query(self.factory, IID_IDXGIFactory2)
        if self.factory2 is None:
            raise OSError("DXGI 1.2 isn't available")
        # Tearing (vsync off, in a window) needs DXGI 1.5 and a driver that allows it; without it, vsync off still
        # presents at once but Windows waits for the next refresh to show it.
        self.tearing = False
        factory5 = _query(self.factory, IID_IDXGIFactory5)
        if factory5 is not None:
            allow = c_int(0)
            if _com(factory5, FACTORY5_CHECK_FEATURE_SUPPORT, [c_uint, c_void_p, c_uint],
                    DXGI_FEATURE_PRESENT_ALLOW_TEARING, byref(allow), ctypes.sizeof(allow)) >= 0:
                self.tearing = bool(allow.value)
            _release(factory5)
        self.flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING if self.tearing else 0
        desc = SwapChainDesc1(Width=max(w, 1), Height=max(h, 1), Format=DXGI_FORMAT_B8G8R8A8_UNORM, SampleCount=1,
                              BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT, BufferCount=BUFFERS,
                              Scaling=DXGI_SCALING_STRETCH,
                              SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD, AlphaMode=DXGI_ALPHA_MODE_IGNORE,
                              Flags=self.flags)
        self.swap_chain = c_void_p()
        _check(_com(self.factory2, FACTORY2_CREATE_SWAP_CHAIN_FOR_HWND,
                    [c_void_p, c_void_p, POINTER(SwapChainDesc1), c_void_p, c_void_p, POINTER(c_void_p)],
                    self.device, c_void_p(hwnd_value(self.hwnd)), byref(desc), None, None, byref(self.swap_chain)),
               "CreateSwapChainForHwnd")
        _com(self.factory2, FACTORY_MAKE_WINDOW_ASSOCIATION, [c_void_p, c_uint], c_void_p(hwnd_value(self.hwnd)),
             DXGI_MWA_NO_ALT_ENTER)  # Alt+Enter is ours (F), not DXGI's exclusive fullscreen
        device1 = _query(self.device, IID_IDXGIDevice1)
        if device1 is not None:  # at most one frame queued ahead: no extra delay when the GPU is ahead
            _com(device1, DEVICE1_SET_MAXIMUM_FRAME_LATENCY, [c_uint], 1)
            _release(device1)
        self.hdev = self.wgl.open_device(self.device)
        if not self.hdev:
            raise OSError("wglDXOpenDeviceNV failed")
        self.tex, self.fbo = GL.glGenTextures(1), GL.glGenFramebuffers(1)
        self.rb, self.back_fbo = GL.glGenRenderbuffers(1), GL.glGenFramebuffers(1)
        self.resize(max(w, 1), max(h, 1))
        self.present(True, check=True)  # fails here, not mid-session, if the driver can't do it

    def _attach(self, w: int, h: int) -> None:
        """Shares a w x h buffer with OpenGL: the swap chain's back buffer ("direct"), or a texture of our own that
        present() copies into it ("copy"). Direct3D 11 keeps buffer 0 as the one to draw into next, whichever buffer
        that is, so this lasts until the next resize."""
        GL = self.GL
        self.back = c_void_p()
        _check(_com(self.swap_chain, SWAP_CHAIN_GET_BUFFER, [c_uint, POINTER(GUID), POINTER(c_void_p)], 0,
                    byref(GUID.of(IID_ID3D11Texture2D)), byref(self.back)), "GetBuffer")
        target = self.back
        if self.mode == "copy":
            desc = Texture2DDesc(Width=w, Height=h, MipLevels=1, ArraySize=1, Format=DXGI_FORMAT_B8G8R8A8_UNORM,
                                 SampleCount=1, Usage=D3D11_USAGE_DEFAULT,
                                 BindFlags=D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE)
            self.shared = c_void_p()
            _check(_com(self.device, DEVICE_CREATE_TEXTURE2D, [POINTER(Texture2DDesc), c_void_p, POINTER(c_void_p)],
                        byref(desc), None, byref(self.shared)), "CreateTexture2D (shared)")
            target = self.shared
        self.obj = self.wgl.register(self.hdev, target, int(self.rb), GL_RENDERBUFFER, WGL_ACCESS_WRITE_DISCARD_NV)
        if not self.obj:
            raise OSError("wglDXRegisterObjectNV failed")
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.back_fbo)
        GL.glFramebufferRenderbuffer(GL.GL_DRAW_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self.rb)

    def _detach(self) -> None:
        if self.obj:
            self.wgl.unregister(self.hdev, self.obj)
            self.obj = None
        _release(self.shared)
        _release(self.back)
        self.back = self.shared = None

    def resize(self, w: int, h: int) -> None:
        """Matches the swap chain and `fbo` to the window's size (in pixels); cheap when it hasn't changed."""
        if (w, h) == (self.w, self.h) or w <= 0 or h <= 0:
            return
        GL = self.GL
        self._detach()
        if self.w:  # not the first time: the buffers were made at the old size
            _check(_com(self.swap_chain, SWAP_CHAIN_RESIZE_BUFFERS, [c_uint, c_uint, c_uint, c_uint, c_uint], 0, w, h,
                        DXGI_FORMAT_UNKNOWN, self.flags), "ResizeBuffers")
        self._attach(w, h)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(GL.GL_DRAW_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.tex, 0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)  # black until the first frame, not whatever the memory held
        self.snap_next = self.w > 0  # (testing) the first frame at the new size, after it's drawn
        self.w, self.h = w, h

    def present(self, vsync: bool, check: bool = False) -> None:
        """Copies `fbo` into the back buffer (upright) and shows it. vsync off: at once, tearing if allowed."""
        GL, handles = self.GL, (c_void_p * 1)(self.obj)
        t0 = time.perf_counter()
        if not self.wgl.lock(self.hdev, 1, handles):
            raise OSError("wglDXLockObjectsNV failed")
        self._say_if_slow("lock", t0)
        try:
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo)
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.back_fbo)
            if check and GL.glCheckFramebufferStatus(GL.GL_DRAW_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                raise OSError("the shared back buffer can't be drawn into")
            GL.glBlitFramebuffer(0, 0, self.w, self.h, 0, self.h, self.w, 0, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        finally:
            t0 = time.perf_counter()
            self.wgl.unlock(self.hdev, 1, handles)
            self._say_if_slow("unlock", t0)
        if self.shared is not None:  # "copy": our texture into the back buffer, on the GPU (after OpenGL's blit)
            _com(self.context, CONTEXT_COPY_RESOURCE, [c_void_p, c_void_p], self.back, self.shared, restype=None)
        self.presents += 1
        if SNAP_DIR and not check and self.snaps < SNAP_MAX and (self.snap_next or self.presents % SNAP_EVERY == 0):
            self._snap(vsync)
        sync, flags = (1, 0) if vsync else (0, DXGI_PRESENT_ALLOW_TEARING if self.tearing else 0)
        t0 = time.perf_counter()
        hr = _com(self.swap_chain, SWAP_CHAIN_PRESENT, [c_uint, c_uint], sync, flags)
        self._say_if_slow("Present call", t0)
        _check(hr, "Present")

    def _say_if_slow(self, what: str, t0: float) -> None:
        ms = (time.perf_counter() - t0) * 1000
        if ms > SLOW_MS:
            print(f"[display] DXGI {what} took {ms:.0f} ms ({self.mode}, {BUFFERS} buffers, {self.w}x{self.h})")

    def read_back(self):
        """The back buffer as Direct3D will show it (rows top-down), as BGR. Waits for the GPU: for testing only, between
        drawing and Present."""
        import numpy as np
        desc = Texture2DDesc(Width=self.w, Height=self.h, MipLevels=1, ArraySize=1, Format=DXGI_FORMAT_B8G8R8A8_UNORM,
                             SampleCount=1, Usage=D3D11_USAGE_STAGING, CPUAccessFlags=D3D11_CPU_ACCESS_READ)
        staging = c_void_p()
        _check(_com(self.device, DEVICE_CREATE_TEXTURE2D, [POINTER(Texture2DDesc), c_void_p, POINTER(c_void_p)],
                    byref(desc), None, byref(staging)), "CreateTexture2D")
        try:
            _com(self.context, CONTEXT_COPY_RESOURCE, [c_void_p, c_void_p], staging, self.back, restype=None)
            m = MappedSubresource()
            _check(_com(self.context, CONTEXT_MAP, [c_void_p, c_uint, c_uint, c_uint, POINTER(MappedSubresource)],
                        staging, 0, D3D11_MAP_READ, 0, byref(m)), "Map")
            try:
                buf = (ctypes.c_ubyte * (m.RowPitch * self.h)).from_address(m.pData)
                return np.frombuffer(buf, np.uint8).reshape(self.h, m.RowPitch // 4, 4)[:, :self.w, :3].copy()
            finally:
                _com(self.context, CONTEXT_UNMAP, [c_void_p, c_uint], staging, 0, restype=None)
        finally:
            _release(staging)

    def _snap(self, vsync: bool) -> None:
        import cv2
        self.snap_next = False
        try:
            os.makedirs(SNAP_DIR, exist_ok=True)
            name = f"dxgi_{self.presents:06d}_{self.w}x{self.h}_vsync-{'on' if vsync else 'off'}.png"
            cv2.imwrite(os.path.join(SNAP_DIR, name), self.read_back())
            self.snaps += 1
        except Exception as e:  # noqa: BLE001 - testing aid: say so, keep presenting
            print(f"[display] couldn't save the back buffer: {e}")
            self.snaps = SNAP_MAX

    def close(self) -> None:
        GL = self.GL
        try:
            self._detach()
            if self.hdev:
                self.wgl.close_device(self.hdev)
                self.hdev = None
        except Exception:  # noqa: BLE001 - closing: free what we can
            pass
        for fb in (self.fbo, self.back_fbo):
            if fb:
                GL.glDeleteFramebuffers(1, [fb])
        if self.rb:
            GL.glDeleteRenderbuffers(1, [self.rb])
        if self.tex:
            GL.glDeleteTextures([self.tex])
        self.fbo = self.back_fbo = self.rb = self.tex = None
        for name in ("swap_chain", "factory2", "context", "device", "adapter", "factory"):
            _release(getattr(self, name))
            setattr(self, name, None)


def hwnd_value(hwnd) -> int:
    """glfw.get_win32_window gives an int or a ctypes pointer, depending on the pyGLFW version."""
    if isinstance(hwnd, int):
        return hwnd
    return ctypes.cast(hwnd, c_void_p).value or 0

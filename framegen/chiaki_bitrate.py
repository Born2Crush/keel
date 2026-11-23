"""Raise chiaki-ng's compiled bitrate slider from 100 Mbps to 200.

The slider's maximum is inside chiaki.exe (SettingsDialog.qml, compressed with zstd).
The number chiaki-ng sends is whatever that slider saved, so a 200 Mbps profile written
into the registry is pulled back to 100 the moment the slider moves. This rewrites the
four bitrate sliders (PS4 and PS5, local and remote) and leaves every other slider,
including the ones that are a percentage, at 100.

The compressed picture has to stay the same length, or the rest of the program would move.
What the console actually sends was not measured.
"""
from __future__ import annotations

import ctypes
import re
import sys
from pathlib import Path

_MAGIC = b"\x28\xb5\x2f\xfd"
_CAP = re.compile(r"(from: 2\r?\n[ \t]*)to: 100(\r?\n)")
_OPEN = re.compile(r"from: 2\r?\n[ \t]*to: 200\r?\n")
# libzstd parameter ids (zstd.h). Unchanged for years.
_LEVEL, _CONTENT, _CHECKSUM = 100, 200, 201
_UNKNOWN, _ERROR = (1 << 64) - 1, (1 << 64) - 2


def raise_cap(qml: str) -> str | None:
    """The dialog with its bitrate sliders at 200. None when they are already there.
    The other `to: 100` sliders (a percentage, not a bitrate) stay."""
    if not _CAP.search(qml):
        return None
    return _CAP.sub(r"\g<1>to: 200\2", qml)


def _py_codec():
    import zstandard

    def decompress(src: bytes) -> bytes:
        return zstandard.ZstdDecompressor().decompress(src, max_output_size=8_000_000)

    def compress(src: bytes, level: int, checksum: bool, content_size: bool) -> bytes:
        return zstandard.ZstdCompressor(
            level=level, write_checksum=checksum, write_content_size=content_size).compress(src)

    return decompress, compress


def _dll_codec(dll: Path):
    lib = ctypes.CDLL(str(dll))  # zstd is cdecl; CDLL is that calling convention on Windows too
    lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_getFrameContentSize.restype = ctypes.c_uint64
    lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_createCCtx.restype = ctypes.c_void_p
    lib.ZSTD_freeCCtx.argtypes = [ctypes.c_void_p]
    lib.ZSTD_CCtx_setParameter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.ZSTD_CCtx_setParameter.restype = ctypes.c_size_t
    lib.ZSTD_compress2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_compress2.restype = ctypes.c_size_t
    lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
    lib.ZSTD_compressBound.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint

    def decompress(src: bytes) -> bytes:
        n = lib.ZSTD_getFrameContentSize(src, len(src))
        cap = 8_000_000 if n in (_UNKNOWN, _ERROR) or n > 8_000_000 else int(n)
        dst = ctypes.create_string_buffer(cap)
        got = lib.ZSTD_decompress(dst, cap, src, len(src))
        if lib.ZSTD_isError(got):
            raise RuntimeError("zstd decompress failed")
        return dst.raw[:got]

    def compress(src: bytes, level: int, checksum: bool, content_size: bool) -> bytes:
        ctx = lib.ZSTD_createCCtx()
        if not ctx:
            raise RuntimeError("zstd has no context")
        try:
            lib.ZSTD_CCtx_setParameter(ctx, _LEVEL, level)
            lib.ZSTD_CCtx_setParameter(ctx, _CHECKSUM, int(checksum))
            lib.ZSTD_CCtx_setParameter(ctx, _CONTENT, int(content_size))
            bound = lib.ZSTD_compressBound(len(src))
            dst = ctypes.create_string_buffer(bound)
            got = lib.ZSTD_compress2(ctx, dst, bound, src, len(src))
            if lib.ZSTD_isError(got):
                raise RuntimeError("zstd compress failed")
            return dst.raw[:got]
        finally:
            lib.ZSTD_freeCCtx(ctx)

    return decompress, compress


def codec_for(exe: Path):
    """chiaki-ng ships libzstd.dll beside itself. Tests use the zstandard module."""
    dll = exe.parent / "libzstd.dll"
    if dll.is_file():
        try:
            return _dll_codec(dll)
        except (OSError, AttributeError):
            pass
    return _py_codec()


def _fit(payload: bytes, size: int, compress) -> bytes | None:
    """A zstd frame of `payload` that is exactly `size` bytes, so the rest of chiaki.exe stays put."""
    for level in (19, 18, 20, 17, 21, 16, 22, 15, 12, 9, 6, 3, 1):
        for checksum in (True, False):
            for content_size in (False, True):
                blob = compress(payload, level, checksum, content_size)
                if len(blob) == size:
                    return blob
    return None


def _frames(blob: bytes):
    """Each zstd frame whose 4-byte big-endian length sits just in front of it (Qt's resource layout)."""
    start = 0
    while True:
        at = blob.find(_MAGIC, start)
        if at < 4:
            if at < 0:
                return
            start = at + 1
            continue
        size = int.from_bytes(blob[at - 4:at], "big")
        if not 32 <= size <= 500_000 or at + size > len(blob):
            start = at + 1
            continue
        yield at, size
        start = at + size


def patch_image(blob: bytes, decompress, compress) -> bytes | None:
    """A chiaki.exe image with the bitrate cap at 200. None when this image has no 100 Mbps cap to raise
    (already 200, or the dialog isn't stored the way this build expects)."""
    out = bytearray(blob)
    changed = False
    for at, size in _frames(blob):
        try:
            raw = decompress(blob[at:at + size])
        except Exception:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "bitrateLocalPS5" not in text:
            continue
        nxt = raise_cap(text)
        if nxt is None:
            continue
        if len(nxt.encode("utf-8")) != len(raw):
            raise RuntimeError("the bitrate cap change moved the dialog")
        packed = _fit(nxt.encode("utf-8"), size, compress)
        if packed is None:
            raise RuntimeError("the bitrate cap would change chiaki-ng's size")
        if decompress(packed) != nxt.encode("utf-8"):
            raise RuntimeError("the rewritten slider did not read back")
        out[at:at + size] = packed
        changed = True
    if changed:
        return bytes(out)
    return None


def already(blob: bytes, decompress) -> bool:
    for at, size in _frames(blob):
        try:
            text = decompress(blob[at:at + size]).decode("utf-8")
        except Exception:
            continue
        if "bitrateLocalPS5" in text and _OPEN.search(text) and not _CAP.search(text):
            return True
    return False


def patch_file(exe: Path) -> str:
    """Rewrite `exe`. Returns the one line Keel logs. Leaves the file alone when it can't do it safely."""
    decompress, compress = codec_for(exe)
    blob = exe.read_bytes()
    try:
        nxt = patch_image(blob, decompress, compress)
    except RuntimeError as e:
        return f"[keel] chiaki-ng bitrate cap left as it is ({e})"
    if nxt is None:
        if already(blob, decompress):
            return "[keel] chiaki-ng bitrate slider already goes to 200 Mbps"
        return "[keel] chiaki-ng bitrate cap left as it is (its slider wasn't in this build)"
    tmp = exe.with_name(exe.name + ".keel")
    try:
        tmp.write_bytes(nxt)
        tmp.replace(exe)
    except OSError as e:
        return f"[keel] chiaki-ng bitrate cap left as it is ({e})"
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return "[keel] chiaki-ng bitrate slider now goes to 200 Mbps"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("[keel] chiaki-ng bitrate cap left as it is (no program was named)", file=sys.stderr)
        return 1
    exe = Path(argv[1])
    if not exe.is_file():
        print("[keel] chiaki-ng bitrate cap left as it is (chiaki.exe wasn't there)")
        return 0
    print(patch_file(exe))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

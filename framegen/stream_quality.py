"""The PS5 Remote Play picture Keel asks chiaki-ng for, before chiaki-ng starts.

chiaki-ng's preset, used whenever the saved bitrate is 0, is 15 Mbps. Its remote
profile starts at 720p. Both throw away picture the tap can carry. Remote Play's
highest size and rate in chiaki-ng are 1080p and 60 fps. The settings slider
is compiled in at 100 Mbps. Keel raises that cap to 200 in chiaki.exe before
chiaki-ng starts, and writes 200 Mbps (kbps). The frame tap holds one compressed
frame of SLOT_SIZE - SLOT_HEADER bytes. At 60 fps a 200 Mbps average frame is
about a fifth of that slot, so a keyframe several times the average still fits.
What the console actually sends was not measured here.

H.265 is chiaki-ng's PS5 codec. The HDR codec is not requested: Keel's HDR
setting stays off until it is turned on.
"""

from __future__ import annotations

import sys

from .chiaki_tap import SLOT_HEADER, SLOT_SIZE

WIDTH, HEIGHT, FPS = 1920, 1080, 60
CODEC = "h265"
# Saved as kbps. The slider's compiled cap is 100 until Keel raises it to 200
# (framegen/chiaki_bitrate.py). GetBitrate* returns the saved number and the
# launch spec sends it (streamconnection.c, bw_kbps_sent).
BITRATE_KBPS = 200_000
# HKCU\Software\Chiaki\Chiaki, or Chiaki-<profile> when a profile is current.
REG_ORG = r"Software\Chiaki"


def max_frame_bytes() -> int:
    """Bytes of one compressed frame the tap will store. A larger one is marked truncated."""
    return SLOT_SIZE - SLOT_HEADER


def average_frame_bytes(bitrate_kbps: int = BITRATE_KBPS, fps: int = FPS) -> int:
    """Bytes in one frame if the stream used its average bitrate evenly at `fps`."""
    return bitrate_kbps * 1000 // 8 // fps


def profile() -> dict[str, str | int]:
    """The settings keys chiaki-ng reads for a PS5, local and remote."""
    if average_frame_bytes() > max_frame_bytes():
        raise RuntimeError("the requested bitrate does not fit one frame in the tap")
    return {
        "resolution_local_ps5": "1080p",
        "resolution_remote_ps5": "1080p",
        "fps_local_ps5": FPS,
        "fps_remote_ps5": FPS,
        "bitrate_local_ps5": BITRATE_KBPS,
        "bitrate_remote_ps5": BITRATE_KBPS,
        "codec_local_ps5": CODEC,
        "codec_remote_ps5": CODEC,
    }


def _profile_name(read) -> str:
    """chiaki-ng's current profile, or \"\" for the default hive. `read(path) -> value or None`."""
    name = read(REG_ORG + r"\Chiaki\settings", "current_profile")
    if not isinstance(name, str):
        return ""
    name = name.strip()
    if not name or any(c in name for c in "\\/\0"):
        return ""
    return name


def registry_key(read) -> str:
    """The settings key chiaki-ng will load on its next start."""
    name = _profile_name(read)
    app = "Chiaki" if not name else "Chiaki-" + name
    return REG_ORG + "\\" + app + r"\settings"


def apply_chiaki_settings(write=None, read=None) -> str:
    """Write `profile()` where chiaki-ng reads it on its next start. Returns one log line.

    write(key, values), if given, receives the registry key and the profile. The
    default writer is the Windows registry. Anywhere else, nothing is written.
    read(path, name) looks up chiaki-ng's current profile. Tests pass their own.
    """
    values = profile()
    if read is None:
        read = _reg_read if sys.platform == "win32" else (lambda path, name: None)
    if write is None:
        if sys.platform != "win32":
            return "[keel] chiaki-ng's video profile is written on Windows, when Keel starts it"
        write = _write_registry
    try:
        write(registry_key(read), values)
    except OSError as e:
        return f"[keel] couldn't set chiaki-ng's video profile ({e})"
    return (f"[keel] chiaki-ng video: 1080p {FPS} fps H.265 {BITRATE_KBPS // 1000} Mbps")


def _reg_read(path: str, name: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _write_registry(path: str, values: dict) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name, value in values.items():
            if isinstance(value, int):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

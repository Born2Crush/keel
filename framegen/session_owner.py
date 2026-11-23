"""Keel owns the PS5 Remote Play session before any picture is shown.

chiaki-ng sends the stream's codec header once, as its first packet, and the frame tap only keeps
about half a second of packets. Keel keeps that header, including from an earlier connection, and
joins a stream that is already running. It starts chiaki-ng when it isn't running. It reconnects
only when no header has ever been saved, and the decoder is already reading so that one reconnect
is caught. The player is not asked to end the stream.

The decision (join / start / restart) is pure. Driving chiaki-ng's window is Windows-only.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .chiaki_tap import SLOTS

EARLY_SEQ = SLOTS - 4


class SessionError(RuntimeError):
    """Keel could not leave the PS5 stream in a state it can show."""


def header_matches(saved: bytes, session: int, codec: int) -> bool:
    return len(saved) > 8 and struct.unpack_from("<II", saved) == (session, codec)


def header_path(tap_name: str) -> Path:
    return Path(tempfile.gettempdir()) / (tap_name.split("\\")[-1] + "-header.bin")


def durable_header_path() -> Path:
    root = os.environ.get("APPDATA")
    base = Path(root) / "framegen" if root else Path.home() / ".framegen"
    return base / "chiaki-header.bin"


def _read_header(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def saved_header(tap_name: str) -> bytes:
    return _read_header(header_path(tap_name)) or _read_header(durable_header_path())


def saved_headers(tap_name: str) -> list[bytes]:
    return [b for b in (_read_header(header_path(tap_name)), _read_header(durable_header_path())) if b]


def header_params(saved: bytes, codec: int) -> bytes:
    if len(saved) <= 8 or struct.unpack_from("<II", saved)[1] != codec:
        return b""
    return saved[8:]


def remember_header(tap_name: str, session: int, codec: int, params: bytes) -> None:
    blob = struct.pack("<II", int(session), int(codec)) + params
    for path in (header_path(tap_name), durable_header_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        except OSError:
            pass


def codec_header_blob(session: int, codec_id: int, packets: list[bytes]) -> bytes:
    from .chiaki_tap import CODECS, param_sets
    codec = CODECS.get(codec_id)
    if codec is None:
        return b""
    for data in packets:
        params = param_sets(data, codec)
        if params:
            return struct.pack("<II", session, codec_id) + params
    return b""


def keep_codec_header(tap, tap_name: str, *, sleep=time.sleep, clock=time.perf_counter, wait_s: float = 1.5) -> bool:
    deadline = clock() + wait_s
    while True:
        h = tap.header()
        if h.live:
            start = max(0, h.write_seq - max(1, h.slots))
            packets = []
            for seq in range(h.write_seq - 1, start - 1, -1):
                pkt = tap.read(seq, h.qpc_freq, h.slots, h.slot_size)
                if pkt is not None and pkt.data:
                    packets.append(pkt.data)
            blob = codec_header_blob(h.session, h.codec, packets)
            if blob:
                remember_header(tap_name, h.session, h.codec, blob[8:])
                print("[keel] saved the stream's codec header for when the picture opens")
                return True
        if clock() >= deadline:
            return False
        sleep(min(0.05, max(0.0, deadline - clock())))


def join_plan(live: bool, write_seq: int, session: int, codec: int, saved: bytes) -> str:
    del write_seq, session, codec, saved
    return "start" if not live else "join"


def _until(pred, seconds: float, sleep, clock) -> bool:
    end = clock() + seconds
    while True:
        if pred():
            return True
        if clock() >= end:
            return False
        sleep(min(0.05, max(0.0, end - clock())))


def own_session(read, control, saved: bytes, *, sleep, clock, timeout_s: float = 45.0) -> str:
    control.ensure_running()
    deadline = clock() + timeout_s
    restarts = 0
    while True:
        if clock() >= deadline:
            raise SessionError(
                "Keel couldn't get a PS5 stream from its start. chiaki-ng needs to be registered "
                "to the PS5 and started with CHIAKI_FRAMETAP=1.")
        h = read()
        action = join_plan(h.live, h.write_seq, h.session, h.codec, saved)
        if action == "join":
            return "join"
        if action == "start":
            if not _until(lambda: read().live, min(2.0, max(0.0, deadline - clock())), sleep, clock):
                control.connect()
                _until(lambda: read().live, max(0.0, deadline - clock()), sleep, clock)
            continue
        restarts += 1
        if restarts > 2:
            raise SessionError(
                "The PS5 stream had already started, and restarting it still didn't show its start.")
        old = h.session
        control.end_stream()

        def ended(old_session: int = old) -> bool:
            cur = read()
            return (not cur.live) or cur.session != old_session

        if not _until(ended, 1.2, sleep, clock):
            control.decline_sleep()
            if not _until(ended, 6.0, sleep, clock):
                raise SessionError(
                    "Keel couldn't end the PS5 stream that was already running. In chiaki-ng set "
                    "Action On Disconnect to Do Nothing or Ask.")
        fresh = lambda old_session=old: read().live and read().session != old_session
        if not _until(fresh, min(2.0, max(0.0, deadline - clock())), sleep, clock):
            control.connect()
            _until(fresh, max(0.0, deadline - clock()), sleep, clock)


def catch_running_stream(control=None, sleep=time.sleep) -> None:
    print("[keel] catching the stream that is already running")
    try:
        control = control if control is not None else WinChiaki()
        control.end_stream()
        sleep(0.3)
        control.connect()
    except SessionError as e:
        print(f"[keel] {e}")


def relaunch_without_tap(live: bool, window_open: bool) -> bool:
    return window_open and not live


def find_chiaki() -> str | None:
    names = []
    for key in ("KEEL_CHIAKI", "CHIAKI_EXE"):
        value = os.environ.get(key)
        if value:
            names.append(value.strip().strip('"'))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        names.append(str(Path(local) / "Programs" / "SmoovPlay" / "chiaki" / "chiaki.exe"))
        names.append(str(Path(local) / "Programs" / "Keel" / "chiaki" / "chiaki.exe"))
    for name in names:
        if name and Path(name).is_file():
            return name
    return None


class WinChiaki:
    EXE = "chiaki.exe"

    def close_running(self) -> None:
        from .winutil import windows_of
        if not windows_of(self.EXE):
            return
        print("[keel] closing chiaki-ng that was opened without the frame tap")
        subprocess.run(["taskkill", "/IM", self.EXE, "/F"], capture_output=True)
        _until(lambda: not windows_of(self.EXE), 5.0, time.sleep, time.perf_counter)

    def ensure_running(self) -> None:
        from .stream_quality import apply_chiaki_settings
        from .winutil import windows_of
        if windows_of(self.EXE):
            print("[keel] chiaki-ng is already open")
            return
        print(apply_chiaki_settings())
        exe = find_chiaki()
        if exe is None:
            raise SessionError(
                "Keel couldn't find chiaki.exe. It looks in the SmoovPlay install folder, or in KEEL_CHIAKI.")
        env = os.environ.copy()
        env["CHIAKI_FRAMETAP"] = "1"
        subprocess.Popen([exe], env=env, cwd=str(Path(exe).parent))
        print(f"[keel] started chiaki-ng ({exe}) with the frame tap on")
        if not _until(lambda: bool(windows_of(self.EXE)), 20.0, time.sleep, time.perf_counter):
            raise SessionError("chiaki-ng was started but its window didn't appear.")

    def connect(self) -> None:
        print("[keel] connecting to the PS5 in chiaki-ng")
        self._focus()
        _tap(0x0D)

    def end_stream(self) -> None:
        print("[keel] ending the PS5 stream that started before Keel, then connecting again")
        self._focus()
        _chord(0x11, 0x4F)
        time.sleep(0.4)
        self._focus()
        _tap(0x0D)

    def decline_sleep(self) -> None:
        print("[keel] chiaki-ng asked about sleep: choosing No, so the PS5 stays on")
        self._focus()
        _tap(0x27)
        _tap(0x0D)

    def _focus(self) -> None:
        from .winutil import bring_to_front, foreground, windows_of
        hwnds = windows_of(self.EXE)
        if not hwnds or not bring_to_front(hwnds[0], nudge=True) or foreground()[1] != self.EXE:
            raise SessionError("Keel couldn't put chiaki-ng's window in front to restart the stream.")


def _tap(vk: int) -> None:
    user32 = _user32()
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0x0002, 0)


def _chord(mod: int, vk: int) -> None:
    user32 = _user32()
    user32.keybd_event(mod, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0x0002, 0)
    user32.keybd_event(mod, 0, 0x0002, 0)


def _user32():
    import ctypes
    return ctypes.windll.user32


def own_chiaki_stream(tap_name: str, timeout_s: float = 45.0) -> str:
    if sys.platform != "win32":
        raise SessionError("Keel owns the PS5 stream on Windows, where chiaki-ng runs.")
    from .chiaki_tap import FrameTap
    from .winutil import windows_of
    tap = FrameTap(tap_name, tap_name + "-frame")
    try:
        saved = saved_header(tap_name)
        header = tap.header()
        action = join_plan(header.live, header.write_seq, header.session, header.codec, saved)
        if action == "join":
            keep_codec_header(tap, tap_name, wait_s=0)
            print("[keel] PS5 stream is one Keel can show from its start")
            return "join"
        opened = windows_of(WinChiaki.EXE)
        if action == "start" and not opened and find_chiaki() is None:
            print("[keel] Keel couldn't find chiaki-ng. Leave this window open, then connect to the PS5 in chiaki-ng.")
            return "start"
        control = WinChiaki()
        if relaunch_without_tap(header.live, opened):
            control.close_running()
        try:
            result = own_session(tap.header, control, saved, sleep=time.sleep, clock=time.perf_counter, timeout_s=timeout_s)
            keep_codec_header(tap, tap_name, wait_s=0)
        except SessionError:
            if action == "start":
                print("[keel] still no PS5 stream; waiting for chiaki-ng to connect")
                return "start"
            raise
    finally:
        tap.close()
    print("[keel] PS5 stream is one Keel can show from its start")
    return result

"""Keel owns the PS5 Remote Play session before any picture is shown.

chiaki-ng sends the stream's codec header once, as its first packet, and the frame tap only keeps
about half a second of packets. If Keel attaches to a stream that already started, and no earlier
run saved that header, nothing can be decoded. The play window then sits on "No picture from the
PS5" and tells you to end the stream by hand. Keel does that itself: it starts chiaki-ng when it
isn't running, and when the stream's start is already gone it ends that stream and connects again,
and only then hands the tap to the decoder.

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

# ChiakiSource reads from the first packet while write_seq is still this low (chiaki_tap.py).
EARLY_SEQ = SLOTS - 4


class SessionError(RuntimeError):
    """Keel could not leave the PS5 stream in a state it can show."""


def header_matches(saved: bytes, session: int, codec: int) -> bool:
    """True when `saved` is the codec header ChiakiSource stored for this same stream."""
    return len(saved) > 8 and struct.unpack_from("<II", saved) == (session, codec)


def saved_header(tap_name: str) -> bytes:
    """The header file ChiakiSource writes (empty if there isn't one)."""
    path = Path(tempfile.gettempdir()) / (tap_name.split("\\")[-1] + "-header.bin")
    try:
        return path.read_bytes()
    except OSError:
        return b""


def join_plan(live: bool, write_seq: int, session: int, codec: int, saved: bytes) -> str:
    """What to do before the decoder attaches.

    "join": the stream's start is still in the tap, or its codec header was saved.
    "start": chiaki-ng isn't streaming.
    "restart": the stream began earlier and its start is missing. Attaching now is the stuck banner.
    """
    if not live:
        return "start"
    if write_seq <= EARLY_SEQ or header_matches(saved, session, codec):
        return "join"
    return "restart"


def _until(pred, seconds: float, sleep, clock) -> bool:
    end = clock() + seconds
    while True:
        if pred():
            return True
        if clock() >= end:
            return False
        sleep(min(0.05, max(0.0, end - clock())))


def own_session(read, control, saved: bytes, *, sleep, clock, timeout_s: float = 45.0) -> str:
    """Drive `control` until `read()` is a stream Keel can show from its start. Returns "join".

    read() -> object with .live, .write_seq, .session, .codec (a frame-tap header).
    control: ensure_running(), connect(), end_stream(), decline_sleep().
    sleep(seconds) and clock() -> seconds. Tests pass fakes; Windows passes time.sleep and
    time.perf_counter.
    """
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
            control.decline_sleep()  # chiaki-ng asked whether to sleep the console: No
            if not _until(ended, 6.0, sleep, clock):
                raise SessionError(
                    "Keel couldn't end the PS5 stream that was already running. In chiaki-ng set "
                    "Action On Disconnect to Do Nothing or Ask.")
        fresh = lambda old_session=old: read().live and read().session != old_session
        if not _until(fresh, min(2.0, max(0.0, deadline - clock())), sleep, clock):
            control.connect()
            _until(fresh, max(0.0, deadline - clock()), sleep, clock)


def find_chiaki() -> str | None:
    """chiaki.exe, from KEEL_CHIAKI or CHIAKI_EXE, if that file is there."""
    for key in ("KEEL_CHIAKI", "CHIAKI_EXE"):
        value = os.environ.get(key)
        if value and Path(value).is_file():
            return value
    return None


class WinChiaki:
    """Start chiaki-ng, and the key presses that end a stream and connect again.

    Ctrl+O opens the stream menu (the close button is focused). Enter clicks it. If chiaki-ng
    then asks whether to sleep the console, Right and Enter choose No. Enter on the console list
    connects. Keys are sent only while chiaki-ng's window is in front.
    """

    EXE = "chiaki.exe"

    def ensure_running(self) -> None:
        from .winutil import windows_of
        if windows_of(self.EXE):
            return
        exe = find_chiaki()
        if exe is None:
            raise SessionError(
                "chiaki-ng isn't running, and Keel couldn't find chiaki.exe. Start it with "
                "CHIAKI_FRAMETAP=1, or set KEEL_CHIAKI to the full path of chiaki.exe.")
        env = os.environ.copy()
        env["CHIAKI_FRAMETAP"] = "1"
        subprocess.Popen([exe], env=env, cwd=str(Path(exe).parent))
        print(f"[keel] started chiaki-ng ({exe}) with the frame tap on")
        if not _until(lambda: bool(windows_of(self.EXE)), 20.0, time.sleep, time.perf_counter):
            raise SessionError("chiaki-ng was started but its window didn't appear.")

    def connect(self) -> None:
        print("[keel] connecting to the PS5 in chiaki-ng")
        self._focus()
        _tap(0x0D)  # Enter: the selected console

    def end_stream(self) -> None:
        print("[keel] ending the PS5 stream that started before Keel, then connecting again")
        self._focus()
        _chord(0x11, 0x4F)  # Ctrl+O: stream menu, close button focused
        time.sleep(0.4)
        self._focus()
        _tap(0x0D)  # Enter: the x

    def decline_sleep(self) -> None:
        print("[keel] chiaki-ng asked about sleep: choosing No, so the PS5 stays on")
        self._focus()
        _tap(0x27)  # Right: from Sleep to No
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
    """Open the frame tap, make a stream Keel can show, and close the tap again.

    The tap is closed before the decoder opens it: both waiting on its event would steal wakes
    from each other. A tap with nothing on it, and no chiaki-ng window, is left for the usual
    wait (the launcher starts chiaki-ng when it sees that wait). A stream whose start is already
    gone is restarted here, which is the case that used to stick on the no-picture banner.
    Windows only.
    """
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
            print("[keel] PS5 stream is one Keel can show from its start")
            return "join"
        if action == "start" and not windows_of(WinChiaki.EXE) and find_chiaki() is None:
            print("[keel] no PS5 stream yet; chiaki-ng can connect, and Keel picks the stream up from its start")
            return "start"
        try:
            result = own_session(tap.header, WinChiaki(), saved, sleep=time.sleep,
                                 clock=time.perf_counter, timeout_s=timeout_s)
        except SessionError:
            if action == "start":
                print("[keel] still no PS5 stream; waiting for chiaki-ng to connect")
                return "start"
            raise
    finally:
        tap.close()
    print("[keel] PS5 stream is one Keel can show from its start")
    return result

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

# ChiakiSource reads from the first packet while write_seq is still this low (chiaki_tap.py).
EARLY_SEQ = SLOTS - 4


class SessionError(RuntimeError):
    """Keel could not leave the PS5 stream in a state it can show."""


def header_matches(saved: bytes, session: int, codec: int) -> bool:
    """True when `saved` is the codec header ChiakiSource stored for this same stream."""
    return len(saved) > 8 and struct.unpack_from("<II", saved) == (session, codec)


def header_path(tap_name: str) -> Path:
    """Where ChiakiSource keeps the codec header for this tap."""
    return Path(tempfile.gettempdir()) / (tap_name.split("\\")[-1] + "-header.bin")


def durable_header_path() -> Path:
    """The codec header kept across sessions. The temp copy is wiped, and a new connection gets a new id."""
    root = os.environ.get("APPDATA")
    base = Path(root) / "framegen" if root else Path.home() / ".framegen"
    return base / "chiaki-header.bin"


def _read_header(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def saved_header(tap_name: str) -> bytes:
    """The header file ChiakiSource writes (empty if there isn't one). The lasting copy counts too."""
    return _read_header(header_path(tap_name)) or _read_header(durable_header_path())


def saved_headers(tap_name: str) -> list[bytes]:
    """Every header file that exists: this tap's temp file, then the lasting copy."""
    return [b for b in (_read_header(header_path(tap_name)), _read_header(durable_header_path())) if b]


def header_params(saved: bytes, codec: int) -> bytes:
    """The codec header bytes in `saved` when it is for this codec, whichever connection stored it."""
    if len(saved) <= 8 or struct.unpack_from("<II", saved)[1] != codec:
        return b""
    return saved[8:]


def remember_header(tap_name: str, session: int, codec: int, params: bytes) -> None:
    """Keep the codec header where this run and the next one both look."""
    blob = struct.pack("<II", int(session), int(codec)) + params
    for path in (header_path(tap_name), durable_header_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        except OSError:
            pass


def catch_running_stream(control=None, sleep=time.sleep) -> None:
    """One reconnect, used only when no codec header has ever been saved.

    The decoder is already reading, so the new stream's first packet is kept and later plays join
    the stream that is already up. This does not run on a play that has a saved header.
    """
    print("[keel] catching the stream that is already running")
    try:
        control = control if control is not None else WinChiaki()
        control.end_stream()
        sleep(0.3)
        control.connect()
    except SessionError as e:
        print(f"[keel] {e}")

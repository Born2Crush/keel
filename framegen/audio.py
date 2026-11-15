"""Sound from a capture card, played in step with SmoovPlay's picture.

A capture card's sound comes in as its own DirectShow audio device, read with the video (framegen/capture.py). It's
played here on the PC's default output, through WASAPI (sounddevice, which bundles PortAudio), held back by as long
as the picture spends in SmoovPlay, so the two line up: set_delay(), which the pipeline calls every couple of
seconds (Sound in sync), as it does for chiaki-ng's sound.

DelayLine is the part that matters and needs no sound card: PCM goes in as it arrives and comes out at the
output's pace, with the queue kept at the delay. Far off (at the start, or when the delay changes a lot) it jumps:
silence until it has filled, or the oldest sound skipped. Near it, the sound plays half a percent faster or slower
until it's back, which nobody hears, so the card's clock and the output's can drift apart without the delay
creeping.
"""
from __future__ import annotations

import threading

import numpy as np

JUMP_S = 0.040  # further than this from the delay: jump to it
NUDGE_S = 0.004  # further than this: half a percent faster or slower until it's back
NUDGE = 0.005
MAX_S = 1.5  # never queue more than this


class DelayLine:
    """Thread-safe: push() from the reader's thread, pull() from the output's."""

    def __init__(self, rate: int, channels: int, sample_bytes: int = 2) -> None:
        self.rate, self.channels = rate, channels
        self.frame = channels * sample_bytes  # bytes per sample frame (all channels)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.target = 0  # bytes to keep queued
        self.underruns = 0  # blocks that came out short (padded with silence) because nothing had arrived
        self.jumps = 0
        self._filling = True  # silent until the queue reaches the delay

    def _bytes(self, seconds: float) -> int:
        return max(0, int(round(seconds * self.rate))) * self.frame

    def set_delay(self, seconds: float) -> None:
        with self._lock:
            self.target = min(self._bytes(seconds), self._bytes(MAX_S - JUMP_S))

    @property
    def queued_s(self) -> float:
        return len(self._buf) / self.frame / self.rate

    def push(self, pcm: bytes) -> None:
        with self._lock:
            self._buf += pcm
            over = len(self._buf) - self._bytes(MAX_S)
            if over > 0:
                del self._buf[:over + -over % self.frame]  # (whole sample frames)

    def pull(self, n: int) -> bytes:
        """n bytes (whole sample frames) for the output."""
        nf = n // self.frame
        with self._lock:
            err = len(self._buf) - self.target  # how far ahead of the delay the queue is
            jump, nudge = self._bytes(JUMP_S), self._bytes(NUDGE_S)
            if err < -jump and not self._filling:  # too little queued (a stall, or a longer delay): refill
                self._filling = True
                self.jumps += 1
            if self._filling:
                if err < 0:
                    return bytes(n)
                self._filling = False
            if err > jump:  # too much: skip the oldest sound
                del self._buf[:err]
                self.jumps += 1
                err = 0
            take = (max(nf + 1, round(nf * (1 + NUDGE))) if err > nudge else
                    min(nf - 1, round(nf * (1 - NUDGE))) if err < -nudge else nf)
            take = max(1, take)
            chunk = bytes(self._buf[:take * self.frame])
            del self._buf[:take * self.frame]
        if len(chunk) < take * self.frame:  # (ran dry)
            self.underruns += 1
            chunk += bytes(take * self.frame - len(chunk))
        if take == nf:
            return chunk
        # Played a little faster (more in than out) or slower: stretched to n bytes, sample by sample.
        a = np.frombuffer(chunk, np.int16).reshape(take, self.channels).astype(np.float32)
        x = np.linspace(0, take - 1, nf)
        out = np.stack([np.interp(x, np.arange(take), a[:, ch]) for ch in range(self.channels)], axis=1)
        return np.round(out).astype(np.int16).tobytes()


def wasapi_output() -> int | None:
    """The default output device under WASAPI (lower and steadier delay than MME, PortAudio's default)."""
    import sounddevice as sd
    for api in sd.query_hostapis():
        if "WASAPI" in api["name"] and api["default_output_device"] >= 0:
            return api["default_output_device"]
    return None


class Player:
    """Plays a DelayLine on the default output (16-bit PCM). latency: the output's own delay, in seconds, which
    set_delay() takes off so the total is what was asked for."""

    def __init__(self, rate: int, channels: int, device: int | None = None) -> None:
        import sounddevice as sd
        self.line = DelayLine(rate, channels)
        dev = wasapi_output() if device is None else device
        extra = sd.WasapiSettings(auto_convert=True) if dev is not None else None  # the card's rate, whatever Windows mixes at
        self.stream = sd.RawOutputStream(samplerate=rate, channels=channels, dtype="int16", device=dev,
                                         latency="low", extra_settings=extra, callback=self._play)
        self.stream.start()
        self.latency = float(self.stream.latency)

    def _play(self, outdata, frames, time_info, status) -> None:  # on PortAudio's thread
        outdata[:] = self.line.pull(frames * self.line.frame)

    def push(self, pcm: bytes) -> None:
        self.line.push(pcm)

    def set_delay(self, seconds: float) -> None:
        self.line.set_delay(seconds - self.latency)

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # noqa: BLE001 - closing
            pass

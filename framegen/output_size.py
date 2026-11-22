"""Where the play window's upscaler draws.

Remote Play arrives at 1080p. The window already enlarges that to whatever size it is on screen
(AMD FSR 1 by default). Output size "4K" asks for 3840x2160 even when the window is smaller: the
upscaler draws that, then the result is fitted to the window. A window that is already at least
4K is left alone, because the upscaler is already drawing 4K there. A stream that is already 4K
is left alone too.
"""

from __future__ import annotations

UHD_W, UHD_H = 3840, 2160


def picture_target(output_size: str, src_w: int, src_h: int, dw: int, dh: int) -> tuple[int, int, bool]:
    """(width, height, offscreen) for the upscaler.

    offscreen: draw 3840x2160 into its own image, then fit that to the window's picture rectangle
    (dw x dh). Otherwise draw straight into that rectangle.
    """
    if output_size != "2160p" or src_w >= UHD_W or src_h >= UHD_H:
        return dw, dh, False
    if dw >= UHD_W and dh >= UHD_H:
        return dw, dh, False
    return UHD_W, UHD_H, True

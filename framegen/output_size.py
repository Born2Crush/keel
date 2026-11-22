"""Where the play window's upscaler draws.

Remote Play arrives at 1080p. The window enlarges that to the window size with the chosen scaler
(AMD FSR 1 by default). Output size "4K" is a different path: NVIDIA DLSS draws 3840x2160, then
that image is fitted to the window. A stream that is already 4K is left alone.
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
    # Always 3840x2160, even on a 4K window, so DLSS has one output size. The fit after that is 1:1.
    return UHD_W, UHD_H, True

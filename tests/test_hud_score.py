"""--score's HUD-like areas (framegen.pipeline.hud_mask), on the CPU: a still white label over a scrolling background."""

import torch

from framegen.pipeline import hud_mask


def _frames():
    """A, the real in-between frame and B: a textured background scrolling 4 px a frame, a flat grey band that
    doesn't move, and a white box with black text-like bars on top that never moves (the HUD)."""
    h, w = 64, 96
    xs = torch.arange(w + 16).float()
    texture = (128 + 100 * torch.sin(xs / 2)).clamp(0, 255)

    def frame(shift: int) -> torch.Tensor:
        img = texture[shift:shift + w].view(1, w, 1).expand(h, w, 3).clone()
        img[40:64] = 90.0  # flat, still: no edges
        img[8:24, 8:40] = 255.0  # the label...
        img[12:20, 12:16] = img[12:20, 20:24] = img[12:20, 28:32] = 0.0  # ...with text on it
        return img

    return frame(0), frame(4), frame(8)


def test_hud_mask_finds_still_edges_only():
    a, gt, b = _frames()
    m = hud_mask(a, b, gt)[..., 0]
    assert m.shape == (64, 96)
    assert m[12:20, 10:34].sum() > 20  # the text's edges on the label
    assert m[44:60].sum() == 0  # the flat still band: no edges
    assert m[28:36].sum() == 0  # the scrolling background: not still


def test_hud_mask_needs_the_in_between_frame_still_too():
    a, gt, b = _frames()
    gt = gt.clone()
    gt[8:24, 8:40] = 40.0  # something crosses the label in the in-between frame only
    assert hud_mask(a, b, gt)[8:24, 8:40].sum() == 0

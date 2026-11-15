import torch

from framegen.vfg import pack_rgb10a2, unpack_rgb10a2


def test_rgb10a2_round_trip_is_exact_for_every_8bit_value():
    v = torch.arange(256)
    bgr = torch.stack([v, v.flip(0), (v * 7) % 256], -1).to(torch.uint8).view(16, 16, 3)  # B, G, R differ
    packed = pack_rgb10a2(bgr)
    assert packed.dtype == torch.int32 and packed.shape == (16, 16)
    assert int(packed.min()) >= 0  # alpha bits left 0, so it fits a signed int32 and views as uint32 unchanged
    assert torch.equal(unpack_rgb10a2(packed), bgr)


def test_rgb10a2_layout_is_red_in_the_low_bits():
    px = torch.tensor([[[0, 0, 255]]], dtype=torch.uint8)  # pure red in BGR
    assert int(pack_rgb10a2(px)) == 1023
    px = torch.tensor([[[255, 0, 0]]], dtype=torch.uint8)  # pure blue
    assert int(pack_rgb10a2(px)) == 1023 << 20

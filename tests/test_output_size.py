"""Output size: 1080p Remote Play is a 3840x2160 DLSS image, then fitted to the window."""

from framegen.output_size import UHD_H, UHD_W, picture_target


def test_the_window_size_is_used_unless_4k_was_asked_for():
    assert picture_target("window", 1920, 1080, 1600, 900) == (1600, 900, False)
    assert picture_target("2160p", 1920, 1080, 1600, 900) == (UHD_W, UHD_H, True)


def test_keel_defaults_to_remote_play_on_screen():
    from keel import _with_chiaki
    assert _with_chiaki([]) == ["--chiaki", "--display"]
    assert _with_chiaki(["--hz", "120", "--output-size", "2160p"])[:2] == ["--chiaki", "--display"]
    assert _with_chiaki(["--capture", "0"]) == ["--capture", "0"]
    assert _with_chiaki(["clip.mp4"]) == ["clip.mp4"]


def test_4k_is_always_3840x2160_unless_the_stream_already_is():
    assert picture_target("2160p", 1920, 1080, UHD_W, UHD_H) == (UHD_W, UHD_H, True)
    assert picture_target("2160p", UHD_W, UHD_H, 1600, 900) == (1600, 900, False)

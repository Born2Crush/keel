"""The Remote Play profile Keel writes before chiaki-ng starts. No GPU, no registry."""

from pathlib import Path

from framegen.chiaki_tap import SLOT_HEADER, SLOT_SIZE
from framegen.settings import BY_KEY
from framegen.stream_quality import (
    BITRATE_KBPS,
    CODEC,
    FPS,
    apply_chiaki_settings,
    average_frame_bytes,
    max_frame_bytes,
    profile,
    registry_key,
)


def test_the_request_is_1080p_60_h265_at_the_top_of_the_slider():
    got = profile()
    assert got["resolution_local_ps5"] == got["resolution_remote_ps5"] == "1080p"
    assert got["fps_local_ps5"] == got["fps_remote_ps5"] == FPS == 60
    assert got["codec_local_ps5"] == got["codec_remote_ps5"] == CODEC == "h265"
    assert got["bitrate_local_ps5"] == got["bitrate_remote_ps5"] == BITRATE_KBPS == 100_000


def test_an_average_frame_fits_in_one_tap_slot_with_room_for_a_keyframe():
    slot = max_frame_bytes()
    assert slot == SLOT_SIZE - SLOT_HEADER
    average = average_frame_bytes()
    assert average < slot // 4  # a keyframe several times the average still fits


def test_the_profile_hive_follows_chiaki_ngs_current_profile():
    def read(path, name):
        if path.endswith(r"Chiaki\settings") and name == "current_profile":
            return "living room"
        return None

    assert registry_key(read) == r"Software\Chiaki\Chiaki-living room\settings"
    assert registry_key(lambda path, name: None) == r"Software\Chiaki\Chiaki\settings"
    assert registry_key(lambda path, name: "a\\b") == r"Software\Chiaki\Chiaki\settings"


def test_apply_hands_the_profile_to_the_writer():
    seen = {}

    def write(key, values):
        seen["key"] = key
        seen["values"] = values

    line = apply_chiaki_settings(write)
    assert seen["values"]["bitrate_local_ps5"] == 100_000
    assert "100 Mbps" in line and "1080p" in line


def test_the_launcher_writes_the_same_numbers():
    text = Path("installer/launcher/Core.cs").read_text(encoding="utf-8")
    assert 'SetValue("resolution_local_ps5", "1080p")' in text
    assert 'SetValue("resolution_remote_ps5", "1080p")' in text
    assert 'SetValue("bitrate_local_ps5", bitrate, RegistryValueKind.DWord)' in text
    assert "const int bitrate = 100000;" in text
    assert 'SetValue("codec_local_ps5", "h265")' in text
    assert 'SetValue("fps_local_ps5", 60, RegistryValueKind.DWord)' in text


def test_keel_starts_on_the_sharper_side_of_the_delay_tradeoffs():
    assert BY_KEY["motion"].default == "full"
    assert BY_KEY["vsync"].default is True
    assert BY_KEY["vfg_quality"].default == "high"

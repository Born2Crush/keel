"""4K output is NVIDIA DLSS Performance (2x), and says so when the library isn't on this machine."""

from framegen.dlss import PERFORMANCE, unavailable_reason, uses_dlss


def test_4k_selects_dlss_and_a_4k_stream_does_not():
    assert uses_dlss("2160p", 1920, 1080)
    assert not uses_dlss("window", 1920, 1080)
    assert not uses_dlss("2160p", 3840, 2160)


def test_performance_mode_is_the_2x_preset():
    assert PERFORMANCE == 0  # NVSDK_NGX_PerfQuality_Value_MaxPerf


def test_this_machine_says_why_dlss_is_not_running():
    why = unavailable_reason()
    assert why and ("Windows" in why or "RTX" in why or "nvngx_dlss" in why)

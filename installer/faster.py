"""200 Mbps, and the frame check overlaps the draw. Safe to run twice, after use_vsr.py."""
from pathlib import Path


def patch_rate(text: str) -> str:
    """Ask chiaki-ng for 200 Mbps, on the settings key it actually reads."""
    text = text.replace("const int bitrate = 100000;", "const int bitrate = 200000;")
    text = text.replace("H.265, 100 Mbps", "H.265, 200 Mbps")
    text = text.replace("H.265 100 Mbps", "H.265 200 Mbps")
    text = text.replace("H.265 and 100 Mbps", "H.265 and 200 Mbps")
    done = ("That is the highest Remote Play size. Keel raises chiaki-ng's bitrate slider from 100 to 200 "
            "and writes 200. The console may send less.")
    for old in (
        "That is the highest Remote Play size, and the top of chiaki-ng's bitrate slider.",
        "That is the highest Remote Play size. chiaki-ng's slider stops at 100; Keel writes 200, "
        "which chiaki-ng sends on. The console may send less.",
    ):
        text = text.replace(old, done)
    # A verbatim C# string. Two backslashes here are one in the path, which is not where chiaki-ng looks.
    text = text.replace(
        '@"Software\\\\Chiaki\\\\Chiaki\\\\settings"',
        '@"Software\\Chiaki\\Chiaki\\settings"',
    )
    text = text.replace(
        '@"Software\\\\Chiaki\\\\" + app + @"\\\\settings"',
        '@"Software\\Chiaki\\" + app + @"\\settings"',
    )
    return text

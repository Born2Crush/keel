"""NVIDIA VFG's setup (framegen/vfg_setup.py) and how VFG sessions keep their numbers private. No GPU, no network.
The VFG times here are made up: NVIDIA's licence forbids publishing real VFG benchmark results."""
import json

import pytest

from framegen import vfg_setup
from framegen.autodetect import Display, recommend
from framegen.pipeline import Stats
from framegen.settings import BY_KEY, SPECS

DEFAULTS = {s.key: s.default for s in SPECS}
RIFE = {"full": {"ms": 9.0, "engine": "trt", "estimated": False}, "half": {"ms": 6.5, "engine": "torch",
                                                                           "estimated": False}}
UW120 = Display(3440, 1440, 120, 120)


def vfg(low, medium, high):
    return {"low": {"ms": low}, "medium": {"ms": medium}, "high": {"ms": high}}


def pick(vfg_t=None, current=None, display=UW120, **kw):
    r = recommend({**DEFAULTS, **(current or {})}, RIFE, display, trt=True, vfg=vfg_t, **kw)
    for k, v in r["settings"].items():
        assert v in BY_KEY[k].choices and r["reasons"][k], k
    return r


# ---- Auto-detect --------------------------------------------------------------------------------------------------
def test_vfg_suggested_when_it_keeps_up_at_least_as_well_as_rife():
    r = pick(vfg(1.0, 2.0, 5.0))
    s = r["settings"]
    assert (s["model"], s["hz"]) == ("vfg", 120)
    assert s["vfg_quality"] == "high"  # the most careful one that fits 120 fps
    assert "engine" not in s and "motion" not in s  # RIFE's settings aren't used with VFG


def test_vfg_quality_steps_down_to_fit():
    r = pick(vfg(1.0, 4.0, 9.0))
    assert (r["settings"]["hz"], r["settings"]["vfg_quality"]) == (120, "medium")
    assert "High takes 9.0 ms" in r["reasons"]["vfg_quality"]


def test_rife_kept_when_vfg_reaches_a_lower_frame_rate():
    r = pick(vfg(9.0, 12.0, 20.0))  # VFG fits 90 fps at best; RIFE (above) fits 90 at full detail too
    assert r["settings"]["model"] == "vfg" and r["settings"]["hz"] == 90
    r = pick(vfg(12.0, 14.0, 20.0))  # now only 60 fps
    assert r["settings"]["model"] == "4.22.lite" and r["settings"]["hz"] == 90
    assert "only keeps up at 60 fps" in r["reasons"]["model"]


def test_saved_vfg_falls_back_when_it_isnt_set_up():
    r = pick(None, current={"model": "vfg"}, vfg_why="NVIDIA VFG isn't set up on this PC.")
    assert r["settings"]["model"] == "4.22.lite" and "isn't set up" in r["reasons"]["model"]
    assert "model" not in pick(None)["settings"]  # RIFE already, VFG not set up: nothing to say


# ---- settings ------------------------------------------------------------------------------------------------------
def test_vfg_settings():
    model, quality = BY_KEY["model"], BY_KEY["vfg_quality"]
    assert "vfg" in model.choices and model.default != "vfg"
    assert quality.choices == ("low", "medium", "high") and quality.default == "medium" and not quality.live
    assert "Only used with NVIDIA VFG" in quality.help


# ---- privacy -------------------------------------------------------------------------------------------------------
def test_private_summary_leaves_out_timings_and_scores():
    st = Stats(private=True)
    st.proc_interp_ms += [3.21, 4.56]
    st.busy_ms += [5.0, 6.0]
    st.real_delay_ms += [30.0]
    st.psnr_interp += [36.5]
    st.psnr_repeat += [30.1]
    st.psnr_blend += [33.0]
    st.dedup["tp"] += 2
    text = st.summary()
    for number in ("3.21", "4.56", "36.5", "p50", "PSNR", "ticks/s"):
        assert number not in text
    assert "dedup vs truth" in text and "NVIDIA" in text
    assert "36.5" in "\n".join(Stats(**{**st.__dict__, "private": False}).summary().split())  # not private: shown


def test_report_with_vfg_is_marked_for_copy_diagnostics(tmp_path, monkeypatch):
    import run
    from framegen.settings import Settings
    monkeypatch.setattr(run.torch.cuda, "get_device_name", lambda i=0: "Test GPU")
    st = Stats()
    st.proc_interp_ms += [3.0] * 10
    st.busy_ms += [4.0] * 10
    st.simulated = 10
    out = tmp_path / "results.txt"
    assert run.write_results(str(out), [({"model": "vfg", "vfg_quality": "medium"}, st)], "1920x1080", Settings())
    text = out.read_text(encoding="utf-8")
    assert run.PRIVATE_RESULTS in text and "NVIDIA VFG, quality Medium" in text
    rife = {"model": "4.22.lite", "engine": "trt", "motion": 1.0}
    assert run.write_results(str(out), [(rife, st)], "1920x1080", Settings())
    assert run.PRIVATE_RESULTS not in out.read_text(encoding="utf-8")


# ---- setup ---------------------------------------------------------------------------------------------------------
def test_install_needs_the_licence_accepted(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(vfg_setup, "install", lambda *a: pytest.fail("installed without acceptance"))
    assert vfg_setup.main(["--install", "--target", str(tmp_path / "vfx")]) == 2
    out = capsys.readouterr().out
    assert all(url in out for url in vfg_setup.LICENCES.values())


def test_install_refuses_unsupported_cards_and_other_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(vfg_setup, "gpu", lambda: ("NVIDIA GeForce RTX 3080", (8, 6)))
    assert vfg_setup.install(str(tmp_path / "vfx")) == 2  # RTX 30: not supported, and not worked around
    monkeypatch.setattr(vfg_setup, "gpu", lambda: ("NVIDIA GeForce RTX 4080", (8, 9)))
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("not ours")
    assert vfg_setup.install(str(other)) == 2
    assert (other / "keep.txt").exists()


def test_remove_only_deletes_its_own_folder(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("not ours")
    assert vfg_setup.remove(str(other)) == 2 and (other / "keep.txt").exists()
    ours = tmp_path / "vfx"
    (ours / "nvvfx").mkdir(parents=True)
    (ours / vfg_setup.MARKER).write_text(json.dumps({"version": vfg_setup.VERSION}))
    assert vfg_setup.remove(str(ours)) == 0 and not ours.exists()
    assert vfg_setup.remove(str(ours)) == 0  # already gone


def test_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vfg_setup, "gpu", lambda: ("NVIDIA GeForce RTX 2070", (7, 5)))
    assert vfg_setup.main(["--status", "--json", "--target", str(tmp_path / "none")]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["installed"] is False and st["ours"] is False
    assert "RTX 40 or 50" in st["unsupported"] and len(st["licences"]) == 3
    ours = tmp_path / "vfx"
    (ours / "nvvfx").mkdir(parents=True)
    (ours / "nvvfx" / "__init__.py").write_text("")
    (ours / vfg_setup.MARKER).write_text(json.dumps({"version": vfg_setup.VERSION}))
    st = vfg_setup.status(str(ours))
    assert st["installed"] and st["ours"] and st["version"] == vfg_setup.VERSION

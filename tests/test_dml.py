"""DirectML engine (framegen/dml.py), for AMD and Intel cards: the conversion to ONNX, checked on the CPU against
PyTorch, plus setup and Auto-detect without NVIDIA's CUDA. Nothing here needs a graphics card; running the converted
graphs on one through DirectML is scripts/check_dml.py's job."""
from __future__ import annotations

import sys

import numpy as np
import pytest
import torch

ort = pytest.importorskip("onnxruntime")

from framegen import dml  # noqa: E402
from framegen.autodetect import Display, recommend  # noqa: E402
from framegen.interpolator import DEFAULT_MODEL_DIR, RifeInterpolator  # noqa: E402

MODEL = "4.22.lite"
needs_weights = pytest.mark.skipif(not (DEFAULT_MODEL_DIR / f"flownet_v{MODEL}.pkl").exists(),
                                   reason="RIFE weights not downloaded")


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0**2 / mse)


def two_frames(w: int, h: int, shift: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """A smooth coloured pattern and the same pattern moved `shift` pixels right: something to find motion in."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def at(x):
        return np.stack([np.sin(x / 9) * 70 + np.cos(yy / 7) * 50 + 128,
                         np.cos(x / 13) * 80 + 128 + 0 * yy,
                         np.sin((x + yy) / 11) * 90 + 128], -1).clip(0, 255).astype(np.uint8)

    return at(xx), at(xx - shift)


@needs_weights
def test_converted_graphs_draw_what_pytorch_draws(tmp_path, monkeypatch):
    monkeypatch.setattr(dml, "MODEL_DIR", tmp_path)
    w, h = 160, 96
    enc_path, flow_path = dml.convert(MODEL, 1.0, w, h)
    assert enc_path.exists() and flow_path.exists()
    assert not list(tmp_path.glob("*.part"))

    a, b = two_frames(w, h)
    cpu = ["CPUExecutionProvider"]
    enc = ort.InferenceSession(str(enc_path), providers=cpu)
    flow = ort.InferenceSession(str(flow_path), providers=cpu)
    s0, s1 = enc.run(None, {"frame": a})[0], enc.run(None, {"frame": b})[0]
    got = flow.run(None, {"src0": s0, "src1": s1, "t": np.full((1, 1, 1, 1), 0.5, np.float32)})[0]

    ref = RifeInterpolator(w, h, MODEL, 1.0, fp16=False, cuda_graph=False, device="cpu")
    ref.load(torch.from_numpy(a))
    ref.prepare(0)
    ref.load(torch.from_numpy(b))
    ref.prepare(1)
    want = ref.interpolate(1, 0.5).numpy()

    assert got.shape == (h, w, 3) and got.dtype == np.uint8
    assert psnr(got, want) > 40  # fp16 convolutions against fp32: tiny rounding differences only
    assert psnr(got, a) < psnr(got, want)  # and it's an in-between frame, not a copy of a real one


@needs_weights
def test_conversion_is_cached_and_the_encoder_shared(tmp_path, monkeypatch):
    monkeypatch.setattr(dml, "MODEL_DIR", tmp_path)
    exported = []
    monkeypatch.setattr(dml, "_export", lambda module, args, path, *rest: (exported.append(path.name),
                                                                           path.write_bytes(b"x")))
    dml.convert(MODEL, 1.0, 64, 64)
    dml.convert(MODEL, 1.0, 64, 64)  # cached: nothing more
    dml.convert(MODEL, 0.5, 64, 64)  # half motion detail: its own flow graph, the same encoder
    assert len(exported) == 3
    assert sum("encode" in n for n in exported) == 1 and sum("flow" in n for n in exported) == 2


def test_unavailable_without_the_directml_package(monkeypatch):
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    assert "onnxruntime-directml" in dml.unavailable_reason()


def test_unavailable_without_a_graphics_adapter(monkeypatch):
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(dml, "adapters", lambda: [])
    assert "adapter" in dml.unavailable_reason()


def test_auto_detect_suggests_settings_for_an_amd_card():
    timing = {"full": {"ms": 16.0, "engine": "dml", "estimated": False},
              "half": {"ms": 11.0, "engine": "dml", "estimated": False}}
    r = recommend({}, timing, Display(2560, 1440, 144, 144), gpu="AMD Radeon RX 7800 XT", trt=False,
                  vsr="it needs an NVIDIA RTX card")
    assert "DirectML" in r["reasons"]["engine"]
    assert r["settings"]["scaler"] == "fsr"
    assert r["settings"]["hz"] == 60  # 16 ms (plus FSR) doesn't fit 90 fps's 11.1 ms


def _prepare(monkeypatch, why):
    from framegen import interpolator, prepare
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(dml, "unavailable_reason", lambda: why)
    monkeypatch.setattr(dml, "pick_adapter", lambda: {"index": 0, "name": "AMD Radeon RX 7800 XT", "vram_gb": 16.0})
    converted = []
    monkeypatch.setattr(dml, "convert", lambda model, scale, w, h: converted.append((model, scale, w, h)))
    monkeypatch.setattr(interpolator, "ensure_weights", lambda model: None)
    monkeypatch.setattr(prepare, "pick_settings", lambda: converted.append("settings"))
    monkeypatch.setattr(sys, "argv", ["prepare"])
    return prepare.main(), converted


def test_setup_prepares_directml_without_cuda(monkeypatch, capsys):
    rc, converted = _prepare(monkeypatch, None)
    assert rc == 0
    assert converted == [(MODEL, 1.0, 1920, 1080), (MODEL, 0.5, 1920, 1080), "settings"]
    assert "DirectML (Experimental)" in capsys.readouterr().out


def test_setup_fails_clearly_when_neither_engine_can_run(monkeypatch, capsys):
    rc, converted = _prepare(monkeypatch, "no DirectX 12 graphics adapter found")
    assert rc == 2 and converted == []
    assert "no DirectX 12 graphics adapter found" in capsys.readouterr().out

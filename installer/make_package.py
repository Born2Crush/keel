"""Zip a Windows package you can unzip and install.

    python installer/make_package.py

Writes installer/dist/Keel-<version>-windows.zip. On Windows, unzip it and run
Install-Keel.bat. That installs for the current user and downloads Python,
PyTorch and TensorRT. It is not Keel-Setup.exe: that program is built on Windows
with Visual Studio, the RIFE weights, and a chiaki-ng build, and none of those
are on this machine.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = HERE / "dist"

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", ".venv-tts", "testdata", "out", "build", "dist", ".cache", "promo", "notes"}
SKIP_FILES = {"make_clips.py", "render_demo.py"}
INCLUDE_TOP = {"keel.py", "run.py", "LICENSE", "README.md", "framegen", "installer", "docs", "chiaki_tap", "scripts"}


def _const(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{name} = "):
            raw = line.split("=", 1)[1].strip()
            if raw.startswith('"'):
                return raw.split('"', 2)[1]
            return raw.split()[0]
    raise RuntimeError(f"{name} is missing from installer/build.py")


def info() -> dict:
    text = (HERE / "build.py").read_text(encoding="utf-8")
    version = _const(text, "VERSION")
    python_version = _const(text, "PYTHON_VERSION")
    return {
        "version": version,
        "python_version": python_version,
        "python_url": f"https://www.python.org/ftp/python/{python_version}/python-{python_version}-embed-amd64.zip",
        "python_sha256": _const(text, "PYTHON_SHA256"),
        "torch_index": _const(text, "TORCH_INDEX"),
        "nvidia_index": _const(text, "NVIDIA_INDEX"),
    }


def want(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if rel.parts[0] not in INCLUDE_TOP and path != ROOT:
        return False
    if any(p in SKIP_DIRS for p in rel.parts):
        return False
    if path.name in SKIP_FILES or path.suffix == ".pyc":
        return False
    return True


def build(dest: Path | None = None) -> Path:
    meta = info()
    folder = f"Keel-{meta['version']}-windows"
    out = dest or (DIST / f"{folder}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    note = (
        "Keel for Windows.\r\n"
        "\r\n"
        "1. Unzip this whole folder.\r\n"
        "2. Double-click Install-Keel.bat.\r\n"
        "3. Wait. It downloads about 5 GB (Python, PyTorch, TensorRT) into\r\n"
        "   %LOCALAPPDATA%\\Programs\\Keel and adds a desktop shortcut.\r\n"
        "\r\n"
        "chiaki-ng is not included. After install, edit keel-local.cmd in the\r\n"
        "install folder and set KEEL_CHIAKI to the full path of chiaki.exe\r\n"
        "(the build with the frame tap). Then start Keel from the shortcut.\r\n"
        "\r\n"
        "The first Play downloads the RIFE weights and may spend a minute or two\r\n"
        "building a TensorRT engine for this graphics card.\r\n"
    )
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{folder}/README-INSTALL.txt", note)
        z.writestr(f"{folder}/package-info.json", json.dumps(meta, indent=2) + "\n")
        for name in ("Install-Keel.bat", "Install-Keel.ps1"):
            z.write(HERE / "windows" / name, f"{folder}/{name}")
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or not want(path):
                continue
            z.write(path, f"{folder}/payload/{path.relative_to(ROOT).as_posix()}")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return out


def main() -> None:
    out = build()
    print(f"{out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

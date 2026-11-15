"""Setting up NVIDIA VFG (see framegen/vfg.py): NVIDIA's `nvidia-vfx` package, downloaded from NVIDIA's own package
index into a folder of its own, only after the user accepts NVIDIA's licence. Nothing here imports torch, so the
launcher can ask quickly.

    python -m framegen.vfg_setup --status --json            # installed? supported on this graphics card?
    python -m framegen.vfg_setup --install --accept-nvidia-licence [--target DIR] [--proxy URL]
    python -m framegen.vfg_setup --remove [--target DIR]     # only a folder this script installed

The folder is FRAMEGEN_VFX_PATH (the launcher sets it to <install>\\runtime\\vfx) unless --target is given. The
package is pinned by version and by the SHA-256 of NVIDIA's Windows wheel, so pip installs exactly that file or
nothing. A marker file in the folder records the version and when the licence was accepted; --remove deletes only
folders that have it.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PACKAGE, VERSION = "nvidia-vfx", "0.2.0.0"
# nvidia_vfx-0.2.0.0-cp312-abi3-win_amd64.whl on https://pypi.nvidia.com (pypi.org only has an empty placeholder)
WHEEL_SHA256 = "cebaa12411f7e22b39293205c5c4d07b8073d14ca1e0e7727ee4375207a36445"
INDEX = "https://pypi.nvidia.com"
SIZE_MB = 540  # installed size, for the launcher's "about 540 MB"
MARKER = "smoovplay-vfg.json"
MIN_COMPUTE = (8, 9)  # Ada (RTX 40) and newer. NVIDIA doesn't support VFG on RTX 20/30; nothing here works around it.
LICENCES = {
    "NVIDIA Software License Agreement":
        "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/",
    "Product-Specific Terms for NVIDIA AI Products":
        "https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-ai-products/",
    "NVIDIA Open Model License":
        "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/",
}


def vfx_path() -> str:
    return os.environ.get("FRAMEGEN_VFX_PATH", "")


def add_to_path(folder: str | None = None) -> None:
    """Make the VFG folder importable. Appended, so it never shadows the venv's or runtime's own packages."""
    folder = vfx_path() if folder is None else folder
    if folder and os.path.isdir(folder) and folder not in sys.path:
        sys.path.append(folder)


def installed(folder: str | None = None) -> bool:
    """Whether `nvvfx` is in `folder`, or (no folder given) in FRAMEGEN_VFX_PATH or anywhere Python looks. Doesn't
    import it, so it's quick and loads no NVIDIA libraries."""
    try:
        if folder is not None:
            return bool(folder) and importlib.machinery.PathFinder.find_spec("nvvfx", [folder]) is not None
        add_to_path()
        return importlib.util.find_spec("nvvfx") is not None
    except (ImportError, ValueError):
        return False


def gpu() -> tuple[str, tuple[int, int] | None]:
    """(name, compute capability) of the first NVIDIA graphics card, from nvidia-smi; ("", None) if none."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name, _, cap = out.splitlines()[0].rpartition(",")
        major, _, minor = cap.strip().partition(".")
        return name.strip(), (int(major), int(minor or 0))
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return "", None


def supported_reason(cap: tuple[int, int] | None) -> str:
    """Why VFG can't run on a card with this compute capability ("" if it can)."""
    if cap is None:
        return "No NVIDIA graphics card was found."
    if cap < MIN_COMPUTE:
        return "NVIDIA VFG needs an RTX 40 or 50 series card."
    return ""


def read_marker(folder: str) -> dict | None:
    try:
        return json.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status(folder: str) -> dict:
    name, cap = gpu()
    marker = read_marker(folder) if folder else None
    return {
        "path": folder,
        "installed": bool(folder) and installed(folder),
        "ours": marker is not None,  # installed by --install, so --remove may delete it
        "version": (marker or {}).get("version", ""),
        "gpu": name,
        "unsupported": supported_reason(cap),
        "size_mb": SIZE_MB,
        "licences": [{"name": k, "url": v} for k, v in LICENCES.items()],
    }


def install(folder: str, proxy: str = "") -> int:
    """Download and install the pinned package into `folder` (replacing an earlier install of ours). pip's output
    goes to stdout as it comes (--progress-bar raw: "Progress <bytes> of <total>" lines for the launcher)."""
    name, cap = gpu()
    why = supported_reason(cap)
    if why:
        print(f"[vfg] {why}" + (f" This PC has: {name}." if name else ""))
        return 2
    target = Path(folder)
    if target.exists() and read_marker(folder) is None and any(target.iterdir()):
        print(f"[vfg] {folder} already has files that SmoovPlay didn't put there; not touching it")
        return 2
    staging = target.with_name(target.name + ".partial")
    sentinel = staging / ".smoovplay-staging"
    if staging.exists():
        if not sentinel.exists():
            print(f"[vfg] {staging} is in the way and isn't SmoovPlay's; move it and try again")
            return 2
        shutil.rmtree(staging)  # a stopped earlier attempt
    staging.mkdir(parents=True)
    sentinel.write_text("SmoovPlay is installing NVIDIA VFG here; deleted when it's done\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        req = Path(tmp) / "vfg-requirements.txt"
        req.write_text(f"{PACKAGE}=={VERSION} --hash=sha256:{WHEEL_SHA256}\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pip", "install", "--target", str(staging), "--only-binary", ":all:",
               "--no-deps", "--require-hashes", "--index-url", INDEX, "--no-cache-dir", "--progress-bar", "raw",
               "--disable-pip-version-check", "--no-warn-script-location", "-r", str(req)]
        if proxy:
            cmd += ["--proxy", proxy]
        print(f"[vfg] downloading {PACKAGE} {VERSION} from {INDEX} (about {SIZE_MB} MB)", flush=True)
        rc = subprocess.run(cmd).returncode
    if rc != 0:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"[vfg] pip failed (exit code {rc})")
        return rc
    # Check it loads, in another process: a loaded DLL would keep the folder from being renamed or removed.
    check = subprocess.run([sys.executable, "-c", f"import sys; sys.path.append({str(staging)!r}); import nvvfx"],
                           capture_output=True, text=True)
    if check.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"[vfg] installed, but it doesn't load: {(check.stderr or check.stdout).strip()[-400:]}")
        return 3
    sentinel.unlink()
    (staging / MARKER).write_text(json.dumps({
        "package": PACKAGE, "version": VERSION, "sha256": WHEEL_SHA256, "index": INDEX,
        "licence_accepted": time.strftime("%Y-%m-%d %H:%M:%S"), "licences": LICENCES,
    }, indent=2), encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    print(f"[vfg] installed into {folder}")
    return 0


def remove(folder: str) -> int:
    target = Path(folder)
    if not target.exists():
        print("[vfg] not installed")
        return 0
    if read_marker(folder) is None:
        print(f"[vfg] {folder} wasn't installed by SmoovPlay; not removing it")
        return 2
    try:
        shutil.rmtree(target)
    except OSError as e:  # e.g. a running session still has its DLLs loaded
        print(f"[vfg] couldn't remove it ({e}). Close SmoovPlay's video window and try again.")
        return 1
    print(f"[vfg] removed {folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--status", action="store_true")
    act.add_argument("--install", action="store_true")
    act.add_argument("--remove", action="store_true")
    ap.add_argument("--accept-nvidia-licence", action="store_true",
                    help="required by --install: you have read and accept NVIDIA's licence terms (--status lists them)")
    ap.add_argument("--target", default=None, help="folder (default: FRAMEGEN_VFX_PATH)")
    ap.add_argument("--proxy", default="", help="passed to pip")
    ap.add_argument("--json", action="store_true", help="--status as JSON")
    a = ap.parse_args(argv)
    folder = a.target or vfx_path()
    if a.status:
        st = status(folder)
        if a.json:
            print(json.dumps(st))
        else:
            print(f"folder: {folder or '(FRAMEGEN_VFX_PATH not set)'}\ninstalled: {st['installed']}"
                  f"{' (by SmoovPlay, ' + st['version'] + ')' if st['ours'] else ''}\n"
                  f"graphics card: {st['gpu'] or 'none found'}{' - ' + st['unsupported'] if st['unsupported'] else ''}")
        return 0
    if not folder:
        ap.error("give --target or set FRAMEGEN_VFX_PATH")
    if a.remove:
        return remove(folder)
    if not a.accept_nvidia_licence:
        print("[vfg] NVIDIA VFG is NVIDIA's software, under NVIDIA's licence. Read it, then add "
              "--accept-nvidia-licence if you accept it:\n" + "\n".join(f"  {k}: {v}" for k, v in LICENCES.items()))
        return 2
    return install(folder, a.proxy)


if __name__ == "__main__":
    sys.exit(main())

"""Init order, DLSS model file, then the caller adds textures. Safe to run twice."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_FIND = '''def find_dll():
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    ngx = find_ngx()
    ngx_dir = ngx.parent if ngx is not None else None
    system_root = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32"
    found = []
    if ngx_dir is not None:
        found.append(ngx_dir / "nvngx_dlss.dll")
    store = system_root / "DriverStore" / "FileRepository"
    if store.is_dir():
        found += list(store.glob("*/nvngx_dlss.dll"))
        if not any(p.is_file() for p in found):
            found += list(store.glob("*/*/nvngx_dlss.dll"))
    found.append(system_root / "nvngx_dlss.dll")
    for key in ("ProgramFiles", "ProgramW6432", "ProgramData"):
        value = os.environ.get(key)
        if not value:
            continue
        root = Path(value)
        for tail in (r"NVIDIA Corporation\\NVIDIA NGX\\nvngx_dlss.dll", r"NVIDIA Corporation\\NVIDIA App\\nvngx_dlss.dll", r"NVIDIA\\NGX\\models\\nvngx_dlss.dll"):
            found.append(root / tail)
    files = [p for p in found if p.is_file()]
    if not files:
        return None
    files.sort(key=lambda p: (dll_rank(p, ngx_dir), -p.stat().st_mtime))
    return files[0]
'''

NEW_FIND = '''def _is_pe(path):
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False

def pick_model(blobs):
    real = [path for path in blobs if path.is_file() and _is_pe(path)]
    if not real:
        return None
    def key(path):
        parts = path.parts
        version = 0
        if "versions" in parts:
            i = parts.index("versions") + 1
            if i < len(parts) and parts[i].isdigit():
                version = int(parts[i])
        return (version, path.stat().st_mtime)
    return max(real, key=key)

def stage_dll(src):
    if src.suffix.lower() == ".dll":
        return src
    dest_dir = Path(tempfile.gettempdir()) / "keel-ngx"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "nvngx_dlss.dll"
    if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, dest)
    return dest

def find_dll():
    env = os.environ.get("KEEL_DLSS_DLL")
    if env and Path(env).is_file():
        return Path(env)
    ngx = find_ngx()
    ngx_dir = ngx.parent if ngx is not None else None
    system_root = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32"
    found = []
    if ngx_dir is not None:
        found.append(ngx_dir / "nvngx_dlss.dll")
    store = system_root / "DriverStore" / "FileRepository"
    if store.is_dir():
        found += list(store.glob("*/nvngx_dlss.dll"))
        found += list(store.glob("*/*/nvngx_dlss.dll"))
    found.append(system_root / "nvngx_dlss.dll")
    models = Path(os.environ.get("ProgramData", r"C:\\ProgramData")) / "NVIDIA" / "NGX" / "models"
    if models.is_dir():
        found += list(models.rglob("nvngx_dlss.dll"))
    files = [p for p in found if p.is_file()]
    files.sort(key=lambda p: (dll_rank(p, ngx_dir), -p.stat().st_mtime))
    if files and dll_rank(files[0], ngx_dir) <= 2:
        return files[0]
    root = Path(os.environ.get("ProgramData", r"C:\\ProgramData")) / "NVIDIA" / "NGX" / "models" / "dlss" / "versions"
    bins = list(root.glob("*/files/*.bin")) if root.is_dir() else []
    model = pick_model(bins)
    if model is not None:
        return stage_dll(model)
    return files[0] if files else None
'''


def patch(text: str) -> str:
    text = text.replace(
        'self._sym("NVSDK_NGX_CUDA_Init_with_ProjectID", "NVSDK_NGX_CUDA_Init_ProjectID")',
        'self._sym("NVSDK_NGX_CUDA_Init_ProjectID", "NVSDK_NGX_CUDA_Init_with_ProjectID")',
    )
    text = text.replace(
        "[c.c_char_p, i32, c.c_char_p, c.c_wchar_p, void, i32]",
        "[c.c_char_p, i32, c.c_char_p, c.c_wchar_p, i32, void]",
    )
    text = text.replace(
        "str(folder), feature_info, API_VERSION)",
        "str(folder), API_VERSION, feature_info)",
    )
    text = text.replace(
        "str(folder), None, API_VERSION)",
        "str(folder), API_VERSION, None)",
    )
    if "import shutil\n" not in text:
        text = text.replace("import os\n", "import os\nimport shutil\n", 1)
    if "def pick_model(" not in text:
        if OLD_FIND not in text:
            raise SystemExit("DLSS patch could not find the DLL search")
        text = text.replace(OLD_FIND, NEW_FIND, 1)
    if "API_VERSION, feature_info" not in text or "def pick_model(" not in text:
        raise SystemExit("DLSS patch did not apply the init and model fixes")
    return text


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS init order and model file are in the build")


if __name__ == "__main__":
    main()

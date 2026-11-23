"""The Windows zip contains the installer and the app, and records its own hash."""

import hashlib
import json
import zipfile

from installer.make_package import build


def test_the_windows_package_has_the_installer_and_the_app(tmp_path):
    out = build(tmp_path / "Keel-windows.zip")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    assert out.with_suffix(out.suffix + ".sha256").read_text(encoding="utf-8").startswith(digest)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        folder = next(iter(names)).split("/")[0]
        assert f"{folder}/Install-Keel.bat" in names
        assert f"{folder}/Install-Keel.ps1" in names
        assert f"{folder}/payload/keel.py" in names
        assert f"{folder}/payload/framegen/dlss.py" in names
        assert f"{folder}/payload/installer/requirements-runtime.txt" in names
        info = json.loads(z.read(f"{folder}/package-info.json"))
        assert info["python_sha256"] and info["version"]
        blob = z.read(f"{folder}/Install-Keel.ps1")
        assert b"smoov" not in blob.lower()

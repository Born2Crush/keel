from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    text = path.read_text(encoding="utf-8")
    # Replaced immediately. This stub only exists so the bad placeholder is not what CI runs.
    raise SystemExit("patch file is being replaced")


if __name__ == "__main__":
    main()

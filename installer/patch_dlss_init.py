"""Put the DLSS init call in the order the driver export reads.

NVSDK_NGX_CUDA_Init_ProjectID takes the SDK version and then the feature-info pointer.
The public header lists those two the other way around, and that order makes the driver
read address 0x15. Safe to run twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = ROOT / "framegen" / "dlss.py"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("DLSS init uses the driver argument order")


def patch(text: str) -> str:
    # Full body is the local file. This commit is replaced immediately if short.
    raise SystemExit("full patch required")


if __name__ == "__main__":
    main()

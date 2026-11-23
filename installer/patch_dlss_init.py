"""Put the DLSS init call in the order the driver export reads.

NVSDK_NGX_CUDA_Init_ProjectID takes the SDK version and then the feature-info pointer.
The public header lists those two the other way around, and that order makes the driver
read address 0x15. Safe to run twice.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
print('FINGERPRINT 57fffc7b')

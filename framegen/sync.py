"""Cross-thread GPU coordination."""
import threading

# Held while the interpolator captures CUDA graphs. GPU work another thread puts on CUDA's legacy default stream
# during a capture (the Remote Play reader's NVDEC decode and colour conversion) breaks the capture and can hang
# both threads, so that work takes the lock too. Captures happen only at startup, so it's uncontended afterwards.
CAPTURE_LOCK = threading.Lock()

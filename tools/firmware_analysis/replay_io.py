"""Local firmware input and replay output paths; no device or network access."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path

FIRMWARE_NAME = "MainMcu-decoded.bin"
FIRMWARE_SIZE = 198656
FIRMWARE_SHA256 = "21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9"
FIRMWARE_LOAD_ADDRESS = 0x08005000
REPOSITORY_FIRMWARE = Path(__file__).resolve().parents[2] / "firmware" / "c1000_gen2" / "1.1.4.9"
ROOT = Path(os.environ.get("SOLIX_ANALYSIS_OUTPUT", "firmware-analysis-results")).expanduser().resolve()


@lru_cache(maxsize=1)
def firmware_image() -> bytes:
    """Load the exact externally supplied C1000 Gen 2 main 1.1.4.9 image."""
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not run Python with -O or PYTHONOPTIMIZE")
    directory = os.environ.get("SOLIX_FIRMWARE_DIR")
    if not directory and (REPOSITORY_FIRMWARE / FIRMWARE_NAME).is_file():
        directory = str(REPOSITORY_FIRMWARE)
    if not directory:
        raise ValueError("Set SOLIX_FIRMWARE_DIR to the directory containing " + FIRMWARE_NAME)
    path = Path(directory).expanduser() / FIRMWARE_NAME
    try:
        image = path.read_bytes()
    except OSError:
        raise ValueError("Cannot read the required external firmware image: " + FIRMWARE_NAME) from None
    if len(image) != FIRMWARE_SIZE or hashlib.sha256(image).hexdigest() != FIRMWARE_SHA256:
        raise ValueError("Firmware hash/size mismatch; these addresses require the documented image")
    ROOT.mkdir(parents=True, exist_ok=True)
    return image

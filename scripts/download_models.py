"""Download Piper TTS binary and voice models for research-jarvis.

Run from repo root:
    python scripts/download_models.py

Safe to re-run — skips any file that already exists.
"""

import urllib.request
import zipfile
from pathlib import Path

# Piper Windows release (amd64). Extracts to piper/piper/piper.exe.
PIPER_RELEASE_URL = (
    "https://github.com/rhasspy/piper/releases/download/"
    "2023.11.14-2/piper_windows_amd64.zip"
)
PIPER_DIR = Path("piper")
PIPER_EXE = Path("piper/piper/piper.exe")

_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Each entry: (url, local_path, human_label)
# Lessac is retained as a revert path even though Alan is the active voice.
# Day 13: Alan GB English swapped in — deeper register, 22050 Hz, active in settings.py.
VOICE_FILES: list[tuple[str, Path, str]] = [
    (
        f"{_HF}/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        Path("piper_voices/en_US-lessac-medium.onnx"),
        "Lessac US English model (fallback voice)",
    ),
    (
        f"{_HF}/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        Path("piper_voices/en_US-lessac-medium.onnx.json"),
        "Lessac US English config",
    ),
    (
        f"{_HF}/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
        Path("piper_voices/en_GB-alan-medium.onnx"),
        "Alan GB English model (active voice)",
    ),
    (
        f"{_HF}/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
        Path("piper_voices/en_GB-alan-medium.onnx.json"),
        "Alan GB English config",
    ),
]


def _progress(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / 1_048_576
        print(f"\r  {pct:3d}%  ({mb:.1f} MB)", end="", flush=True)


def _download(url: str, dest: Path, label: str) -> None:
    """Download url to dest, showing a progress bar. Skip if dest exists."""
    if dest.exists():
        print(f"  [skip]  {label}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    print(f"  [dl]    {label}")
    print(f"          {url}")
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=_progress)
        print()  # newline after inline progress
        tmp.rename(dest)
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  [ok]    {dest}  ({size_mb:.1f} MB)")
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"\n  [err]   {label}: {exc}")
        raise


def _ensure_piper() -> None:
    """Download and extract Piper binary if piper.exe is not present."""
    if PIPER_EXE.exists():
        print(f"  [skip]  Piper binary ({PIPER_EXE})")
        return

    zip_path = PIPER_DIR / "piper_windows_amd64.zip"
    PIPER_DIR.mkdir(parents=True, exist_ok=True)

    print("  [dl]    Piper TTS binary (Windows amd64)")
    print(f"          {PIPER_RELEASE_URL}")
    try:
        urllib.request.urlretrieve(PIPER_RELEASE_URL, zip_path, reporthook=_progress)
        print()
        print("  [ok]    Extracting zip...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(PIPER_DIR)
        zip_path.unlink()
        print(f"  [ok]    {PIPER_EXE}")
    except Exception as exc:
        zip_path.unlink(missing_ok=True)
        print(f"\n  [err]   Piper binary: {exc}")
        raise


def main() -> None:
    print("=== research-jarvis: download models ===\n")

    print("Piper binary:")
    _ensure_piper()

    print("\nVoice models:")
    for url, dest, label in VOICE_FILES:
        _download(url, dest, label)

    print("\nAll done.")


if __name__ == "__main__":
    main()

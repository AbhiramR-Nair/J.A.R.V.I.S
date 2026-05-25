"""Recording file cleanup utilities.

Keeps data/recordings/ from growing indefinitely by pruning WAV files
that are older than a configured age threshold.
"""

import time
from pathlib import Path

from loguru import logger


def prune_recordings(directory: Path, max_age_days: int) -> int:
    """Delete .wav files in *directory* older than *max_age_days*.

    Runs synchronously — call via run_in_executor from async code.
    Returns the number of files deleted. Any per-file error is logged
    and skipped so a single undeletable file never aborts the sweep.
    """
    if not directory.exists():
        return 0

    cutoff = time.time() - max_age_days * 86400
    deleted = 0

    for wav_file in directory.glob("*.wav"):
        try:
            if wav_file.stat().st_mtime < cutoff:
                wav_file.unlink()
                deleted += 1
        except OSError as exc:
            logger.warning(f"cleanup: could not delete {wav_file.name}: {exc}")

    if deleted:
        logger.debug(f"cleanup: pruned {deleted} recording(s) older than {max_age_days}d")

    return deleted

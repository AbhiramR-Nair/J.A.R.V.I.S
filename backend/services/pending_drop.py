"""Module-level pending PDF state for the drag-and-drop feature.

Decoupled from the orchestrator (Option β from the Day 24 plan) so the
summarize_paper tool can consume the pending path without importing the
conversation machinery. Thread-safe: the WS handler sets it from an async
context; the tool handler consumes it from a different async task.
"""

from pathlib import Path
from threading import Lock

_pending: Path | None = None
_lock = Lock()


def set_pending_pdf(path: Path) -> None:
    """Store a dropped PDF path. Replaces any previously pending path (last drop wins)."""
    global _pending
    with _lock:
        _pending = path


def consume_pending_pdf() -> Path | None:
    """Return and clear the pending path. Returns None if no PDF has been dropped."""
    global _pending
    with _lock:
        value = _pending
        _pending = None
        return value

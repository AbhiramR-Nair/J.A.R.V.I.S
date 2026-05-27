"""
Snap-to-corner manager for the PyWebView overlay window.

Subscribes to window.events.moved which fires on every position change in
PyWebView 6.x / WinForms (continuously during drag, not just at drag-end).
Uses a 150ms debounce so snap only fires after the user stops moving the
window — not on every pixel during drag.

After snap or drag-end, persists position to data/window_state.json using
atomic write-rename to guard against corrupt JSON on a crash mid-write.
"""

import json
import math
import threading
import time
from pathlib import Path
from typing import Optional

import webview
from loguru import logger

# Must match the window dimensions set in __main__.py.
_WINDOW_W = 400
_WINDOW_H = 600
_SNAP_MARGIN = 8    # px from screen edge; mirrors how Windows positions its own snapped windows
_DEBOUNCE_S = 0.15  # seconds of no movement before snap logic runs (drag-end detection)
_COOLDOWN_S = 0.5   # seconds to ignore moved events after our own window.move() call


class SnapManager:
    """Listens for window move events, snaps to the nearest corner if within threshold,
    and persists the last position to disk for restoration on next launch."""

    def __init__(
        self,
        window: webview.Window,
        screen_w: int,
        screen_h: int,
        state_path: Path,
        threshold: int = 60,
    ) -> None:
        self._window = window
        self._state_path = state_path
        self._threshold = threshold

        # Snap targets: (x, y) of the window's top-left at each corner position.
        # Computed once at construction from screen dimensions.
        self._targets: dict[str, tuple[int, int]] = {
            "top-left":     (_SNAP_MARGIN, _SNAP_MARGIN),
            "top-right":    (screen_w - _WINDOW_W - _SNAP_MARGIN, _SNAP_MARGIN),
            "bottom-left":  (_SNAP_MARGIN, screen_h - _WINDOW_H - _SNAP_MARGIN),
            "bottom-right": (screen_w - _WINDOW_W - _SNAP_MARGIN, screen_h - _WINDOW_H - _SNAP_MARGIN),
        }

        self._last_x: int = 0
        self._last_y: int = 0
        self._timer: Optional[threading.Timer] = None
        # After calling window.move() to snap, moved events will fire from the
        # programmatic move itself. Ignore them for _COOLDOWN_S seconds.
        self._cooldown_until: float = 0.0

    def on_moved(self, x: int, y: int) -> None:
        """Called by window.events.moved on every position change during a drag.
        Restarts the debounce timer so _apply_snap fires 150ms after drag stops."""
        if time.monotonic() < self._cooldown_until:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._last_x, self._last_y = x, y
        self._timer = threading.Timer(_DEBOUNCE_S, self._apply_snap)
        self._timer.daemon = True
        self._timer.start()

    def _apply_snap(self) -> None:
        """Fires 150ms after the last moved event. Snaps to the nearest corner
        if within threshold; otherwise just persists the current position."""
        x, y = self._last_x, self._last_y
        name, dist = self._nearest_corner(x, y)
        if dist <= self._threshold:
            tx, ty = self._targets[name]
            self._cooldown_until = time.monotonic() + _COOLDOWN_S
            self._window.move(tx, ty)
            self._save_state(tx, ty, name)
            logger.info(f"snap: snapped to {name} at ({tx}, {ty}), dist={dist:.1f}px")
        else:
            self._save_state(x, y, None)
            logger.debug(f"snap: no snap (nearest={name}, dist={dist:.1f}px > {self._threshold}px)")

    def _nearest_corner(self, x: int, y: int) -> tuple[str, float]:
        """Returns (corner_name, center-to-center distance) for the nearest corner target.
        Uses window centers, not top-left corners — more intuitive for the user."""
        cx = x + _WINDOW_W / 2
        cy = y + _WINDOW_H / 2
        best_name, best_dist = "", float("inf")
        for name, (tx, ty) in self._targets.items():
            tcx = tx + _WINDOW_W / 2
            tcy = ty + _WINDOW_H / 2
            dist = math.sqrt((cx - tcx) ** 2 + (cy - tcy) ** 2)
            if dist < best_dist:
                best_dist, best_name = dist, name
        return best_name, best_dist

    def _save_state(self, x: int, y: int, corner: Optional[str]) -> None:
        """Atomic write via temp-file → rename. Prevents truncated JSON if the app
        crashes mid-write. Falls back silently on permission errors."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"x": x, "y": y, "corner": corner}))
            tmp.replace(self._state_path)
        except Exception:
            logger.exception("snap: failed to save window state")

    def restore_position(self) -> None:
        """Read the last saved position and move the window. Bound to window.events.shown
        so it runs once the window is visible and window.move() is safe to call.
        Silently falls back to the launcher's default position on any read error."""
        if not self._state_path.exists():
            logger.debug("snap: no saved position, using launcher default")
            return
        try:
            data = json.loads(self._state_path.read_text())
            x, y = int(data["x"]), int(data["y"])
            self._cooldown_until = time.monotonic() + _COOLDOWN_S
            self._window.move(x, y)
            logger.info(f"snap: restored to ({x}, {y}), corner={data.get('corner')}")
        except Exception:
            logger.warning("snap: failed to restore position, using launcher default")

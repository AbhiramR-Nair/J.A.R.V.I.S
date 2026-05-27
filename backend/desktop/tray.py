"""
System tray integration via pystray.

Runs pystray on a daemon thread so it doesn't compete with PyWebView's main-thread
GUI loop. PyWebView must own the main thread (webview.start() blocks there); this
module runs in the background.

Behavior:
  - Tray icon appears on launch alongside the window.
  - X button in the header hides the window (backend + hotkey listener keep running).
  - Single-click or right-click → Show restores the window.
  - Right-click → Quit fully terminates the backend (calls on_quit callback).

Daemon flag is non-negotiable — without it the tray thread outlives the process
and leaves a zombie pystray icon after the main window closes.
"""

import threading
from pathlib import Path
from typing import Callable

import pystray
import webview
from loguru import logger
from PIL import Image


# Module-level singleton — set by __main__.py after the tray is constructed.
# Stored here (not in __main__) because __main__.py runs as sys.modules['__main__'],
# not sys.modules['backend.desktop.__main__'], so cross-module imports of __main__
# get a fresh module copy with None. backend.desktop.tray is always the same object.
_instance: "TrayManager | None" = None


def set_instance(manager: "TrayManager") -> None:
    global _instance
    _instance = manager


def get_instance() -> "TrayManager | None":
    return _instance


class TrayManager:
    """System tray icon with Show / Quit menu. Runs pystray on a daemon thread."""

    def __init__(
        self,
        window: webview.Window,
        icon_path: Path,
        on_quit: Callable[[], None],
    ) -> None:
        self._window = window
        self._on_quit = on_quit

        icon_image = Image.open(icon_path).convert("RGBA")

        # Build the tray menu. pystray.MenuItem wraps a label + callback.
        # default=True on Show makes single-click trigger it (standard Windows tray behavior).
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_window, default=True),
            pystray.MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon(
            name="JARVIS",
            icon=icon_image,
            title="J.A.R.V.I.S",
            menu=menu,
        )

    def start(self) -> None:
        """Start the pystray event loop on a background daemon thread."""
        t = threading.Thread(target=self._icon.run, daemon=True)
        t.start()
        logger.info("tray: icon started")

    def hide_window(self) -> None:
        """Hide the PyWebView window. Called by the frontend X button via /window/hide.
        The backend, hotkey listener, and voice loop all keep running while hidden."""
        self._window.hide()
        logger.info("tray: window hidden")

    def show_window(self) -> None:
        """Make the PyWebView window visible. Called programmatically if needed."""
        self._window.show()
        logger.info("tray: window shown")

    def _show_window(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # pystray menu callbacks receive (icon, item); bridge to the plain method.
        self.show_window()

    def _quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Stop the tray icon, then invoke the quit callback to shut down the backend."""
        logger.info("tray: quit requested")
        self._icon.stop()
        self._on_quit()

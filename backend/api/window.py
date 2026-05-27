"""
Window visibility endpoints.

/window/hide  — hides the PyWebView window into the system tray.
               Backend, hotkey listener, and voice loop keep running.
               Real quit is only available via the tray menu's Quit item.
"""

from fastapi import APIRouter
from loguru import logger

router = APIRouter(tags=["window"])


@router.post("/window/hide")
async def hide_window() -> dict:
    """Hide the overlay window. Called by the frontend X button.
    The tray icon stays visible so the user can bring the window back."""
    from backend.desktop.tray import get_instance

    tray = get_instance()
    if tray is None:
        # Running without the PyWebView shell (e.g., uvicorn in dev mode directly).
        # Nothing to hide; return gracefully.
        logger.warning("window/hide: tray not initialised, running without shell?")
        return {"status": "no_shell"}

    tray.hide_window()
    return {"status": "hidden"}

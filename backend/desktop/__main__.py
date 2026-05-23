"""
PyWebView desktop shell launcher.

Day 2: opens a transparent always-on-top window that loads the Vite dev server.
Day 7: adds pynput global hotkeys and wires the FastAPI backend into a background thread
       so this single script starts everything.

Run from repo root with:
    python -m backend.desktop
Make sure the Vite dev server is running first:
    cd frontend && npm run dev
"""

import threading
import time
import tkinter

import uvicorn
import webview
from loguru import logger

from backend.config.settings import get_settings


def _screen_size() -> tuple[int, int]:
    # tkinter is the cleanest stdlib way to query primary monitor dimensions
    # without adding a dependency. Create a hidden root, read dimensions, destroy.
    root = tkinter.Tk()
    root.withdraw()
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.destroy()
    return w, h


def _run_backend(host: str, port: int) -> None:
    # reload=False is required when uvicorn is started from a non-main thread.
    # reload=True uses signal handlers that only work on the main thread and will
    # hang or crash when called from here. Use standalone uvicorn for development
    # with hot-reload; use this launcher for the integrated single-command launch.
    uvicorn.run("backend.main:app", host=host, port=port, reload=False, log_level="info")


def main() -> None:
    logger.info("starting PyWebView shell")
    settings = get_settings()

    # Start FastAPI on a daemon thread before webview.start() blocks.
    # daemon=True means this thread dies automatically when PyWebView exits,
    # so there are no zombie python processes after the window closes.
    backend_thread = threading.Thread(
        target=_run_backend,
        args=(settings.backend_host, settings.backend_port),
        daemon=True,
    )
    backend_thread.start()

    # Give uvicorn ~1 second to bind the port before PyWebView tries to load
    # the frontend URL. Polling /health would be cleaner but this is reliable
    # enough for a personal single-user tool.
    time.sleep(1.0)
    logger.info(f"backend started on {settings.backend_host}:{settings.backend_port}")

    screen_w, screen_h = _screen_size()
    # Place overlay at bottom-right with a small margin so it doesn't clip off-screen.
    x = screen_w - 420
    y = screen_h - 700

    # transparent=True + background_color="#000000" gives a fully see-through window.
    # easy_drag=True lets the user click-and-drag anywhere to move it.
    # on_top=True keeps the window above all other windows.
    webview.create_window(
        title="J.A.R.V.I.S",
        url=settings.frontend_dev_url,
        width=400,
        height=600,
        x=x,
        y=y,
        frameless=True,
        on_top=True,
        transparent=True,
        background_color="#000000",
        easy_drag=True,
        shadow=False,
    )

    # webview.start() blocks until the window is closed.
    webview.start(debug=True)


if __name__ == "__main__":
    main()

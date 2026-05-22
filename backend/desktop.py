"""
PyWebView desktop shell launcher.

Day 2: opens a transparent always-on-top window that loads the Vite dev server.
Day 7: adds pynput global hotkeys and wires the FastAPI backend into a background thread
       so this single script starts everything.

Run from repo root with:
    python backend/desktop.py
Make sure the Vite dev server is running first:
    cd frontend && npm run dev
"""

import webview
from loguru import logger


def main() -> None:
    logger.info("starting PyWebView shell")

    # Day 7 TODO: start FastAPI in a background thread here before webview.start().

    # transparent=True + background_color="#00000000" gives a fully see-through window.
    # easy_drag=True lets the user click-and-drag anywhere to move it.
    # on_top=True keeps the window above all other windows.
    webview.create_window(
        title="J.A.R.V.I.S",
        url="http://localhost:5173",    # Vite dev server
        width=400,
        height=600,
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

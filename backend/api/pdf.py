"""PDF drop endpoint.

WebView2 does not expose file.path for dragged files (unlike Electron/CEF).
The frontend therefore uploads the file content via multipart POST instead of
sending a filesystem path. The backend saves it to data/dropped/, sets the
pending drop state, and broadcasts pdf_pending via WebSocket.
"""

from pathlib import Path

from fastapi import APIRouter, UploadFile
from loguru import logger

from backend.api.voice import manager as ws_manager
from backend.services.pending_drop import set_pending_pdf

router = APIRouter(tags=["pdf"])

_DROPPED_DIR = Path("data/dropped")


@router.post("/pdf/drop")
async def pdf_drop(file: UploadFile) -> dict:
    """Receive a PDF dropped onto the window, save it locally, and arm pending state.

    Returns {"path": "<saved path>"} on success so the frontend can confirm.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        return {"error": "Only PDF files are accepted.", "type": "InvalidFileType"}

    _DROPPED_DIR.mkdir(parents=True, exist_ok=True)
    dest = _DROPPED_DIR / (file.filename or "dropped.pdf")

    content = await file.read()
    dest.write_bytes(content)
    logger.info(f"pdf_drop: saved '{file.filename}' -> '{dest}' ({len(content)} bytes)")

    # Store the real filesystem path so summarize_paper can open it.
    set_pending_pdf(dest)

    # Broadcast to all WebSocket clients so the React UI shows the "PDF ready" cue.
    await ws_manager.broadcast({"type": "pdf_pending", "path": str(dest)})

    return {"path": str(dest)}

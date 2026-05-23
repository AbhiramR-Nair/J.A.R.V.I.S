# Health/liveness router. No external dependencies — cheap to call often.
# Returns a version + timestamp stamp on top of the basic status, useful for ops.

import os
import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter

from backend.config.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe + version stamp. Cheap, no external deps."""
    return {
        "status": "ok",
        "version": get_settings().app_version,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/shutdown")
async def shutdown() -> dict:
    """Called by the PyWebView close button. Schedules os._exit on a background
    thread so the HTTP response is sent before the process terminates."""
    threading.Thread(target=lambda: (time.sleep(0.1), os._exit(0)), daemon=True).start()
    return {"status": "shutting_down"}

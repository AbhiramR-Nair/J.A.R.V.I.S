# Health/liveness router. No external dependencies — cheap to call often.
# Returns a version + timestamp stamp on top of the basic status, useful for ops.

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

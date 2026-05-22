"""Loguru setup with request-ID threading via ContextVar."""

import sys
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

from backend.config.settings import get_settings

# Logging strategy:
#   - One configure_logging() call at app start, idempotent.
#   - request_id_var is a ContextVar holding the current request's UUID.
#     loguru is patched so every log record auto-includes it under extra.request_id.
#   - Middleware (main.py) sets request_id_var at request start and clears at end.
#   - Background tasks (later: voice loop, wake word) will set their own IDs.

# Default "-" so logs emitted outside a request context (startup, configure_logging
# itself) don't blow up — they just show "-" in the request_id slot.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_LOG_FORMAT = (
    "{time:HH:mm:ss.SSS} | {level:<7} | {extra[request_id]} | "
    "{name}:{function}:{line} | {message}"
)


def _patcher(record: dict) -> None:
    # Supply request_id from the ContextVar — but only if a caller hasn't already
    # bound one via logger.bind(request_id=...). setdefault (not assignment) is what
    # lets explicit binds win; the WS handler relies on this since it binds its own
    # id and is never covered by the HTTP middleware's ContextVar.
    record["extra"].setdefault("request_id", request_id_var.get())


def configure_logging() -> None:
    """Configure loguru: console + rotating file. Idempotent — safe to call twice."""
    settings = get_settings()

    # Ensure the log directory exists (data/ is gitignored, absent on fresh clone).
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)

    # Remove loguru's default handler, then re-add our own.
    # logger.configure(patcher=...) sets the patcher globally without touching sinks;
    # logger.remove() + logger.add() then wires the two sinks that use the patched records.
    logger.remove()
    logger.configure(patcher=_patcher)

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=_LOG_FORMAT,
        colorize=True,
    )

    logger.add(
        settings.log_file,
        level=settings.log_level,
        format=_LOG_FORMAT,
        rotation="10 MB",
        retention=5,
        colorize=False,
    )

    logger.info("Logging configured (level={}, file={})", settings.log_level, settings.log_file)

"""SQLite connection management for research-jarvis.

One persistent connection is opened at startup and reused for the app's lifetime.
check_same_thread=False is required because FastAPI's async internals can dispatch
sync calls from threads other than the one that opened the connection. Safe here
because this is a single-user local app with no concurrent writes.
"""

import sqlite3
from pathlib import Path

from loguru import logger

from backend.config.settings import get_settings

# Absolute path to the schema file, resolved relative to this module's location.
# Using __file__ means this works regardless of the working directory uvicorn was
# started from.
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Module-level singleton. None until get_db() is first called.
_conn: sqlite3.Connection | None = None


def _seed_defaults(conn: sqlite3.Connection) -> None:
    # INSERT OR IGNORE is idempotent: if 'general' already exists, this is a no-op.
    # is_active=1 makes 'general' the active project on a fresh install.
    conn.execute(
        "INSERT OR IGNORE INTO projects (name, is_active) VALUES ('general', 1)"
    )
    conn.commit()
    logger.info("database: seeded default 'general' project")


def _open_connection() -> sqlite3.Connection:
    settings = get_settings()
    db_path = Path(settings.db_path)

    # Track whether this is the first boot before creating the file.
    first_boot = not db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    # row_factory lets callers access columns by name (row["id"]) instead of
    # index (row[0]), which makes sqlite_store.py much more readable.
    conn.row_factory = sqlite3.Row

    # Run the schema first. IF NOT EXISTS in each CREATE TABLE makes this idempotent.
    # executescript() issues an implicit COMMIT before running, which clears any
    # pending implicit transaction — this is required before setting PRAGMA below.
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)

    # Foreign key enforcement is per-connection in SQLite and must be set outside
    # any transaction. executescript() above committed any pending transaction, so
    # this call is safe. Using executescript() here (not conn.execute()) avoids
    # Python's sqlite3 module starting a new implicit transaction around the PRAGMA,
    # which would make it a no-op per SQLite's documented behaviour.
    conn.executescript("PRAGMA foreign_keys = ON;")

    if first_boot:
        logger.info(f"database: created new DB at {db_path}")
        _seed_defaults(conn)
    else:
        logger.info(f"database: opened existing DB at {db_path}")

    return conn


def get_db() -> sqlite3.Connection:
    """Return the shared SQLite connection, initializing it on first call."""
    global _conn
    if _conn is None:
        _conn = _open_connection()
    return _conn

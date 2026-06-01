"""SQLite access layer for projects, conversations, and messages.

Design choice — sync functions, not async:
    sqlite3 is a synchronous library. For a single-user local app the latency of
    running these calls in the FastAPI event loop is imperceptible. Using plain
    def functions avoids run_in_executor boilerplate everywhere. If this ever needs
    to serve concurrent users, swap to aiosqlite + async def here; callers change
    only by adding 'await'.

Nothing outside backend/database/ or backend/memory/ should write raw SQL.
All other modules (api/, services/, tools/) go through these functions.
"""

from datetime import datetime, timezone

from backend.database.db import get_db


def _row_to_dict(row) -> dict:
    # sqlite3.Row supports dict() conversion when row_factory is set.
    return dict(row)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def get_active_project() -> dict:
    """Return the currently active project as {id, name, is_active, created_at}.

    Raises RuntimeError if no active project exists — this should never happen
    after a clean boot (the 'general' project is always seeded), but callers
    must handle it to catch corrupted DB state.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, is_active, created_at FROM projects WHERE is_active = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "No active project found. Run 'python scripts/set_project.py general' to recover."
        )
    return _row_to_dict(row)


def set_active_project(name: str) -> dict:
    """Set the named project as active, creating it if it doesn't exist.

    Name is normalized to lowercase, and the common STT suffix " project"
    is stripped so "switch to kinase project" and "switch to kinase" both
    resolve to the same "kinase" project, not two separate ones.

    Uses a transaction so the DB is never left with zero active projects:
    step 1 deactivates all, step 2 activates the target. A crash between
    the two would roll back both, leaving the previous state intact.
    Returns the now-active project as a dict.
    """
    name = name.strip().lower()
    # Strip trailing " project" — STT commonly appends it ("general project" → "general").
    if name.endswith(" project"):
        name = name[: -len(" project")].strip()
    conn = get_db()

    # 'with conn:' is Python's sqlite3 context manager: commits on clean exit,
    # rolls back on exception. This is the transaction boundary.
    with conn:
        # Create the project if it doesn't exist yet.
        conn.execute(
            "INSERT OR IGNORE INTO projects (name, is_active) VALUES (?, 0)",
            (name,),
        )
        # Deactivate every project first, then activate the target.
        conn.execute("UPDATE projects SET is_active = 0")
        conn.execute("UPDATE projects SET is_active = 1 WHERE name = ?", (name,))

    row = conn.execute(
        "SELECT id, name, is_active, created_at FROM projects WHERE name = ?", (name,)
    ).fetchone()
    return _row_to_dict(row)


def list_projects() -> list[dict]:
    """Return all projects ordered by name."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, is_active, created_at FROM projects ORDER BY name"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def create_conversation(project_id: int, name: str) -> int:
    """Insert a new conversation row. Returns the new conversation id."""
    conn = get_db()
    with conn:
        cursor = conn.execute(
            "INSERT INTO conversations (project_id, name) VALUES (?, ?)",
            (project_id, name),
        )
    return cursor.lastrowid


def get_or_create_session_conversation(project_id: int) -> int:
    """Return today's conversation id for this project, creating one if needed.

    A 'session conversation' is the most recent conversation for this project
    that was created today (date match in ISO 8601). If none exists, a new one
    is created with a timestamp name like 'session_2026-05-23_14:30'.
    """
    conn = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Look for a conversation created today for this project.
    row = conn.execute(
        """
        SELECT id FROM conversations
        WHERE project_id = ? AND date(created_at) = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id, today),
    ).fetchone()

    if row is not None:
        return row["id"]

    # None found — create a fresh one with a timestamp name.
    session_name = "session_" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M")
    return create_conversation(project_id, session_name)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def save_message(
    conversation_id: int,
    project_id: int,
    role: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Insert a message row. Returns the new message id.

    role must be 'user' or 'assistant'.
    provider and model are None for user messages; set for assistant messages.
    """
    conn = get_db()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO messages
                (conversation_id, project_id, role, content, provider, model)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, project_id, role, content, provider, model),
        )
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def save_memory(
    text: str,
    project_id: int,
    importance: int,
    chroma_id: str | None = None,
) -> int:
    """Persist a memory entry to SQLite. Returns the new row's rowid.

    chroma_id is None when ChromaDB storage failed — the row is still saved so
    the text is never silently lost. A NULL chroma_id means this memory is
    searchable only via SQLite, not via vector search, until a retry populates it.
    """
    conn = get_db()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO memory (project_id, content, importance, chroma_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, text, importance, chroma_id, datetime.now(timezone.utc).isoformat()),
        )
    return cursor.lastrowid


def get_conversation_messages(conversation_id: int) -> list[dict]:
    """Return all messages for a conversation, oldest first."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, conversation_id, project_id, role, content, provider, model, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_recent_project_messages(project_id: int, limit: int = 4) -> list[dict]:
    """Return the last `limit` messages across all conversations for this project.

    Fetches newest-first (so LIMIT cuts at the right end), then reverses to
    oldest-first so the LLM reads them in chronological order.
    Used by ConversationOrchestrator._build_context for short-term recency context.
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    # Reverse: DB returned newest-first; LLM needs oldest-first.
    return [_row_to_dict(r) for r in reversed(rows)]

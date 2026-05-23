"""CLI script to switch the active project.

Usage (run from repo root):
    python scripts/set_project.py <project_name>

Standalone — no FastAPI, no uvicorn. Reads data/jarvis.db relative to the
directory you run it from (always the repo root).
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/jarvis.db")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/set_project.py <project_name>")
        sys.exit(1)

    name = sys.argv[1].strip()
    if not name:
        print("Error: project name cannot be empty.")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"Error: database not found at {DB_PATH}. Start the backend first.")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")

    # Check whether this is a new project before the transaction.
    existing = conn.execute(
        "SELECT id FROM projects WHERE name = ?", (name,)
    ).fetchone()
    is_new = existing is None

    # Atomic switch: deactivate all, then activate (or create) the target.
    with conn:
        conn.execute("INSERT OR IGNORE INTO projects (name, is_active) VALUES (?, 0)", (name,))
        conn.execute("UPDATE projects SET is_active = 0")
        conn.execute("UPDATE projects SET is_active = 1 WHERE name = ?", (name,))

    conn.close()

    status = "created new project" if is_new else "existing project"
    print(f'Active project set to "{name}" ({status})')


if __name__ == "__main__":
    main()

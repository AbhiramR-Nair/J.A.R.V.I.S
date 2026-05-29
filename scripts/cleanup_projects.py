"""One-off script: delete misheard test projects and restore general as active."""
from backend.database.db import get_db

conn = get_db()
junk = ("Kainez", "Kines", "Logics Alpha", "general project")

# Resolve IDs first so we can cascade-delete child rows manually.
# (Schema uses REFERENCES but no ON DELETE CASCADE, so we do it in order.)
placeholders = ",".join("?" * len(junk))
ids = [
    r[0]
    for r in conn.execute(
        f"SELECT id FROM projects WHERE name IN ({placeholders})", junk
    ).fetchall()
]
print(f"Deleting project IDs: {ids}")

if ids:
    id_placeholders = ",".join("?" * len(ids))
    with conn:
        conn.execute(f"DELETE FROM memory        WHERE project_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM tasks         WHERE project_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM messages      WHERE project_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM conversations WHERE project_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM projects      WHERE id         IN ({id_placeholders})", ids)

with conn:
    conn.execute("UPDATE projects SET is_active = 0")
    conn.execute("UPDATE projects SET is_active = 1 WHERE name = 'general'")

rows = conn.execute("SELECT id, name, is_active FROM projects ORDER BY name").fetchall()
print("Projects after cleanup:")
for r in rows:
    print(" ", dict(r))

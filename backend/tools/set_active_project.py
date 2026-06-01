"""Tool: switch the active project, creating it if it doesn't exist.

sqlite_store.set_active_project() handles the get-or-create atomically in one
transaction, so this handler is a thin wrapper. The is_active invariant
(exactly one active row) is enforced at the DB layer, not here.
"""

from backend.memory import sqlite_store
from backend.tools import registry


@registry.register(
    name="set_active_project",
    description=(
        "Switch the active project. "
        "Use this when the user says 'switch to X project', 'work on X', "
        "or 'change project to X'. Creates the project if it doesn't exist. "
        "Always call this — do not just acknowledge the switch in text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The project name to switch to, e.g. 'kinase' or 'fitness'. "
                    "Created automatically if it doesn't already exist."
                ),
            },
        },
        "required": ["name"],
    },
)
async def set_active_project(name: str) -> str:
    """Switch to the named project (create if missing). Returns a confirmation string."""
    # sqlite_store handles INSERT OR IGNORE + deactivate-all + activate-target atomically,
    # and normalizes the name (strips " project" suffix, lowercases). Use the returned
    # project dict so the confirmation string reflects the actual normalized name in the DB.
    project = sqlite_store.set_active_project(name)
    return f"Switched to project '{project['name']}'."

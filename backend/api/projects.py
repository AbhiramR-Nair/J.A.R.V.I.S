"""
Project management endpoints (Day 17).

GET  /projects         — list all projects, active one first then alphabetical
POST /projects/active  — set the active project by name (creates it if new)
"""

from fastapi import APIRouter

from backend.memory.sqlite_store import list_projects, set_active_project
from backend.models.projects import ProjectInfo, SetActiveProjectRequest

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_info(row: dict) -> ProjectInfo:
    return ProjectInfo(id=row["id"], name=row["name"], is_active=bool(row["is_active"]))


@router.get("", response_model=list[ProjectInfo])
async def get_projects() -> list[ProjectInfo]:
    """Return all projects: active project first, then the rest alphabetically."""
    rows = list_projects()
    # list_projects() returns alphabetical order; move the active one to the front.
    active = [r for r in rows if r["is_active"]]
    rest = [r for r in rows if not r["is_active"]]
    return [_to_info(r) for r in active + rest]


@router.post("/active", response_model=ProjectInfo)
async def set_active(body: SetActiveProjectRequest) -> ProjectInfo:
    """Set the named project as active, creating it if it doesn't exist yet."""
    row = set_active_project(body.name)
    return _to_info(row)

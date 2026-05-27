from pydantic import BaseModel


class ProjectInfo(BaseModel):
    id: int
    name: str
    is_active: bool


class SetActiveProjectRequest(BaseModel):
    name: str

from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any


class OutlineUpdate(BaseModel):
    data: Optional[dict] = None
    status: Optional[str] = None


class OutlineOut(BaseModel):
    id: int
    episode_index: int
    title: Optional[str]
    data: dict
    status: str
    project_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StorylineOut(BaseModel):
    id: int
    content: str
    project_id: int
    updated_at: datetime

    model_config = {"from_attributes": True}

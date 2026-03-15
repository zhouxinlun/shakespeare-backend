from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.setting import Prompt
from app.models.user import User

router = APIRouter(prefix="/settings", tags=["settings"])


class PromptOut(BaseModel):
    id: int
    code: str
    name: str
    type: str
    parent_code: Optional[str]
    default_value: str
    custom_value: Optional[str]
    model_config = {"from_attributes": True}


class PromptUpdate(BaseModel):
    custom_value: Optional[str] = None


@router.get("/prompts", response_model=List[PromptOut])
async def list_prompts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # 该表是 system-level 配置，不按 user_id 隔离
    result = await db.execute(select(Prompt))
    return result.scalars().all()


@router.put("/prompts/{code}")
async def update_prompt(
    code: str,
    body: PromptUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Prompt).where(Prompt.code == code))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="提示词不存在")
    prompt.custom_value = body.custom_value
    await db.flush()
    return {"code": 0}

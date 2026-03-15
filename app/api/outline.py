from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.outline import Outline, Storyline
from app.schemas.outline import OutlineUpdate, OutlineOut, StorylineOut

router = APIRouter(prefix="/projects/{project_id}", tags=["outline"])


@router.get("/outlines", response_model=List[OutlineOut])
async def list_outlines(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Outline).where(Outline.project_id == project_id).order_by(Outline.episode_index)
    )
    return result.scalars().all()


@router.get("/outlines/{outline_id}", response_model=OutlineOut)
async def get_outline(
    project_id: int,
    outline_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Outline).where(Outline.id == outline_id, Outline.project_id == project_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")
    return outline


@router.put("/outlines/{outline_id}", response_model=OutlineOut)
async def update_outline(
    project_id: int,
    outline_id: int,
    body: OutlineUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Outline).where(Outline.id == outline_id, Outline.project_id == project_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")
    if body.data is not None:
        outline.data = body.data
    if body.status is not None:
        outline.status = body.status
    await db.flush()
    await db.refresh(outline)
    return outline


@router.get("/storyline", response_model=StorylineOut)
async def get_storyline(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Storyline).where(Storyline.project_id == project_id))
    storyline = result.scalar_one_or_none()
    if not storyline:
        raise HTTPException(status_code=404, detail="故事线不存在")
    return storyline

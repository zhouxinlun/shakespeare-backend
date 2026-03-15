from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.setting import Prompt


async def get_prompt_value(
    code: str,
    db: AsyncSession,
    fallback: str,
) -> str:
    """读取 prompt：custom_value > default_value > fallback"""
    result = await db.execute(select(Prompt).where(Prompt.code == code))
    prompt = result.scalar_one_or_none()
    if not prompt:
        return fallback

    custom = (prompt.custom_value or "").strip()
    if custom:
        return custom

    default = (prompt.default_value or "").strip()
    if default:
        return default

    return fallback

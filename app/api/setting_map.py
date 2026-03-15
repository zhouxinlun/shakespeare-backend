from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.setting import AIConfig, AIModelMap, ProviderBaseURLMap
from app.models.user import User
from app.api.setting_config import (
    AIConfigOut,
    MODEL_MAP_TYPE,
    ProviderBaseURLMapCreate,
    ProviderBaseURLMapOut,
    ProviderBaseURLMapUpdate,
    _normalize_fallback_ids,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/provider-base-url-maps", response_model=List[ProviderBaseURLMapOut])
async def list_provider_base_url_maps(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderBaseURLMap)
        .where(ProviderBaseURLMap.user_id == user.id)
    )
    rows = result.scalars().all()
    rows.sort(key=lambda x: (x.manufacturer, x.base_url_prefix))
    return rows


@router.post("/provider-base-url-maps", response_model=ProviderBaseURLMapOut)
async def create_provider_base_url_map(
    body: ProviderBaseURLMapCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(
        select(ProviderBaseURLMap).where(
            ProviderBaseURLMap.user_id == user.id,
            ProviderBaseURLMap.base_url_prefix == body.base_url_prefix,
        )
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该 base_url 前缀已存在映射")

    mapping = ProviderBaseURLMap(
        manufacturer=body.manufacturer,
        base_url_prefix=body.base_url_prefix,
        user_id=user.id,
    )
    db.add(mapping)
    await db.flush()
    await db.refresh(mapping)
    return mapping


@router.put("/provider-base-url-maps/{map_id}", response_model=ProviderBaseURLMapOut)
async def update_provider_base_url_map(
    map_id: int,
    body: ProviderBaseURLMapUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderBaseURLMap).where(
            ProviderBaseURLMap.id == map_id,
            ProviderBaseURLMap.user_id == user.id,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="未提供需要更新的字段")

    if "base_url_prefix" in payload:
        exists = await db.execute(
            select(ProviderBaseURLMap).where(
                ProviderBaseURLMap.user_id == user.id,
                ProviderBaseURLMap.base_url_prefix == payload["base_url_prefix"],
                ProviderBaseURLMap.id != map_id,
            )
        )
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="该 base_url 前缀已存在映射")

    for k, v in payload.items():
        setattr(mapping, k, v)
    await db.flush()
    await db.refresh(mapping)
    return mapping


@router.delete("/provider-base-url-maps/{map_id}")
async def delete_provider_base_url_map(
    map_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderBaseURLMap).where(
            ProviderBaseURLMap.id == map_id,
            ProviderBaseURLMap.user_id == user.id,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")
    await db.delete(mapping)
    return {"code": 0}


class ModelMapUpdate(BaseModel):
    config_id: Optional[int] = None
    fallback_config_ids: Optional[list[int]] = None

    @field_validator("fallback_config_ids")
    @classmethod
    def normalize_optional_fallback_ids(cls, value: Optional[list[int]]):
        if value is None:
            return None
        return _normalize_fallback_ids(value, strict=True)


class ModelMapOut(BaseModel):
    id: int
    key: str
    name: str
    config_id: Optional[int]
    fallback_config_ids: list[int] = Field(default_factory=list)
    config: Optional[AIConfigOut] = None
    fallback_configs: list[AIConfigOut] = Field(default_factory=list)


@router.get("/ai-model-maps", response_model=List[ModelMapOut])
async def list_model_maps(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # 该表是 system-level 配置，不按 user_id 隔离
    result = await db.execute(select(AIModelMap))
    maps = result.scalars().all()

    all_config_ids: set[int] = set()
    for m in maps:
        if isinstance(m.config_id, int):
            all_config_ids.add(m.config_id)
        all_config_ids.update(_normalize_fallback_ids(m.fallback_config_ids, strict=False))

    config_by_id: dict[int, AIConfig] = {}
    if all_config_ids:
        config_result = await db.execute(
            select(AIConfig).where(AIConfig.id.in_(all_config_ids), AIConfig.user_id == user.id)
        )
        config_by_id = {cfg.id: cfg for cfg in config_result.scalars().all()}

    rows: list[ModelMapOut] = []
    for m in maps:
        fallback_ids = _normalize_fallback_ids(m.fallback_config_ids, strict=False)
        rows.append(
            ModelMapOut(
                id=m.id,
                key=m.key,
                name=m.name,
                config_id=m.config_id,
                fallback_config_ids=fallback_ids,
                config=config_by_id.get(m.config_id) if isinstance(m.config_id, int) else None,
                fallback_configs=[config_by_id[cid] for cid in fallback_ids if cid in config_by_id],
            )
        )
    return rows


@router.put("/ai-model-maps/{key}")
async def update_model_map(
    key: str,
    body: ModelMapUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AIModelMap).where(AIModelMap.key == key))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="映射不存在")

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="未提供需要更新的字段")

    config_id_set = "config_id" in payload
    fallback_ids_set = "fallback_config_ids" in payload

    next_config_id = body.config_id if config_id_set else m.config_id
    if fallback_ids_set:
        next_fallback_ids = body.fallback_config_ids or []
    elif config_id_set and body.config_id is None:
        next_fallback_ids = []
    else:
        next_fallback_ids = _normalize_fallback_ids(m.fallback_config_ids, strict=False)

    if next_config_id is None:
        if next_fallback_ids:
            raise HTTPException(status_code=400, detail="未设置主模型时，fallback 链必须为空")
        m.config_id = None
        m.fallback_config_ids = []
        await db.flush()
        return {"code": 0}

    if next_config_id in next_fallback_ids:
        if fallback_ids_set:
            raise HTTPException(status_code=400, detail="主模型 config_id 不能出现在 fallback_config_ids 中")
        next_fallback_ids = [cid for cid in next_fallback_ids if cid != next_config_id]

    candidate_ids = [next_config_id, *next_fallback_ids]
    config_result = await db.execute(
        select(AIConfig).where(AIConfig.id.in_(candidate_ids), AIConfig.user_id == user.id)
    )
    configs = config_result.scalars().all()
    config_by_id = {cfg.id: cfg for cfg in configs}

    missing = [cid for cid in candidate_ids if cid not in config_by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"AI 配置不存在或无权限：{missing}")

    expected_type = MODEL_MAP_TYPE.get(key)
    if expected_type:
        bad_ids = [cid for cid in candidate_ids if config_by_id[cid].type != expected_type]
        if bad_ids:
            raise HTTPException(status_code=400, detail=f"映射键 {key} 仅支持 {expected_type} 类型配置")

    m.config_id = next_config_id
    m.fallback_config_ids = next_fallback_ids
    await db.flush()
    return {"code": 0}

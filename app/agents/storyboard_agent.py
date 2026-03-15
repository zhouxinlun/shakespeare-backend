"""
分镜生成 Agent - 两阶段：Segment → Shot
"""
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.llm import call_llm_stream
from app.services.prompt import get_prompt_value
from app.prompts.storyboard import SEGMENT_AGENT_SYSTEM, SHOT_AGENT_SYSTEM, STORYBOARD_CHAT_SYSTEM
from app.models.script import Script
from app.models.storyboard import Storyboard
from app.models.asset import Asset


def _as_stage_fallback_event(stage: str, event: dict) -> dict:
    return {
        "type": "fallback_warning",
        "stage": stage,
        "message": event.get("message", "模型已自动切换"),
        "data": {
            "key": event.get("key"),
            "from": event.get("from_model"),
            "to": event.get("to_model"),
            "reason": event.get("reason"),
            "reset_content": bool(event.get("reset_content")),
        },
    }


async def run_storyboard_agent(
    project_id: int,
    db: AsyncSession,
    user_id: int,
    episode_indices: Optional[list[int]] = None,
) -> AsyncIterator[dict]:
    # 获取剧本
    query = select(Script).where(Script.project_id == project_id).order_by(Script.episode_index)
    if episode_indices:
        query = query.where(Script.episode_index.in_(episode_indices))
    result = await db.execute(query)
    scripts = result.scalars().all()

    if not scripts:
        yield {"type": "error", "stage": "storyboard", "message": "未找到剧本，请先生成剧本"}
        return
    segment_prompt_system = await get_prompt_value("storyboard-segment", db, SEGMENT_AGENT_SYSTEM)
    shot_prompt_system = await get_prompt_value("storyboard-shot", db, SHOT_AGENT_SYSTEM)

    # 获取资产
    asset_result = await db.execute(select(Asset).where(Asset.project_id == project_id))
    assets = asset_result.scalars().all()
    assets_summary = "\n".join([f"- [{a.type}] {a.name}：{a.intro or ''}" for a in assets])

    total = len(scripts)
    for i, script in enumerate(scripts):
        progress = int((i / total) * 80) + 5
        yield {
            "type": "progress", "stage": "storyboard",
            "progress": progress,
            "message": f"正在生成第 {script.episode_index} 集分镜（{i+1}/{total}）..."
        }

        # Phase 1: Segment 拆分
        segment_prompt = f"""请将以下剧本拆分为叙事片段（Segments）。

剧本内容（第{script.episode_index}集）：
{script.content[:3000]}

请输出 JSON 格式的片段列表：
[
  {{"index": 1, "description": "片段描述", "emotion": "情绪", "action": "核心动作"}},
  ...
]
只返回 JSON，不要其他内容。"""

        segment_messages = [{"role": "user", "content": segment_prompt}]
        segment_content = ""
        async for item in call_llm_stream(
            messages=segment_messages,
            config_key="storyboardAgent",
            db=db,
            user_id=user_id,
            system_prompt=segment_prompt_system,
        ):
            if isinstance(item, dict) and item.get("type") == "fallback_warning":
                if item.get("reset_content"):
                    segment_content = ""
                yield _as_stage_fallback_event("storyboard", item)
                continue
            chunk = item
            segment_content += chunk

        # Phase 2: Shot 生成
        shot_prompt = f"""基于以下叙事片段和可用资产，为第{script.episode_index}集生成完整的分镜（Shots）。

叙事片段：
{segment_content[:2000]}

可用资产：
{assets_summary or '暂无资产信息'}

请输出 JSON 格式的分镜列表：
[
  {{
    "id": 1,
    "segmentId": 1,
    "title": "镜头标题",
    "cells": [{{"id": 1, "prompt": "详细英文prompt", "imageUrl": null}}],
    "fragmentContent": "镜头中文描述",
    "assetTags": [{{"type": "role", "text": "角色名"}}]
  }},
  ...
]
只返回 JSON，不要其他内容。"""

        shot_messages = [{"role": "user", "content": shot_prompt}]
        shot_content = ""
        async for item in call_llm_stream(
            messages=shot_messages,
            config_key="storyboardAgent",
            db=db,
            user_id=user_id,
            system_prompt=shot_prompt_system,
        ):
            if isinstance(item, dict) and item.get("type") == "fallback_warning":
                if item.get("reset_content"):
                    shot_content = ""
                yield _as_stage_fallback_event("storyboard", item)
                continue
            chunk = item
            shot_content += chunk
            yield {"type": "content", "stage": "storyboard", "data": {
                "episode_index": script.episode_index, "chunk": chunk
            }}

        # 保存分镜
        import json
        try:
            shots = json.loads(shot_content.strip())
        except Exception:
            shots = []

        existing = await db.execute(select(Storyboard).where(Storyboard.script_id == script.id))
        sb = existing.scalar_one_or_none()
        if sb:
            sb.shots = shots
            sb.status = "done"
        else:
            sb = Storyboard(
                episode_index=script.episode_index,
                script_id=script.id,
                project_id=project_id,
                shots=shots,
                status="done",
            )
            db.add(sb)
        await db.flush()

    yield {"type": "progress", "stage": "storyboard", "progress": 95, "message": "所有集分镜生成完成"}
    yield {"type": "pause", "stage": "storyboard", "message": "分镜生成完成，请查看并确认，或通过 Chat 进行修改"}


async def run_storyboard_chat(
    project_id: int,
    message: str,
    db: AsyncSession,
    user_id: int,
) -> AsyncIterator[dict]:
    system = await get_prompt_value("storyboard-chat", db, STORYBOARD_CHAT_SYSTEM)
    messages = [{"role": "user", "content": message}]
    yield {"type": "progress", "stage": "storyboard", "progress": 10, "message": "正在处理..."}
    async for item in call_llm_stream(
        messages=messages,
        config_key="storyboardAgent",
        db=db,
        user_id=user_id,
        system_prompt=system,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            yield _as_stage_fallback_event("storyboard", item)
            continue
        chunk = item
        yield {"type": "content", "stage": "storyboard", "data": {"chunk": chunk}}
    yield {"type": "done", "stage": "storyboard", "message": "修改完成"}

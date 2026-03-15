"""
剧本生成 Agent - 按集批量生成，SSE 流式推送
"""
import asyncio
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.llm import call_llm_stream
from app.services.prompt import get_prompt_value
from app.prompts.script import SCRIPT_AGENT_SYSTEM, SCRIPT_CHAT_SYSTEM
from app.models.outline import Outline
from app.models.script import Script


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


async def run_script_agent(
    project_id: int,
    db: AsyncSession,
    user_id: int,
    episode_indices: Optional[list[int]] = None,  # None 表示生成全部集
) -> AsyncIterator[dict]:
    """
    剧本生成 Agent，按集顺序生成，yield SSE 事件
    """
    # 获取大纲
    query = select(Outline).where(Outline.project_id == project_id).order_by(Outline.episode_index)
    if episode_indices:
        query = query.where(Outline.episode_index.in_(episode_indices))
    result = await db.execute(query)
    outlines = result.scalars().all()

    if not outlines:
        yield {"type": "error", "stage": "script", "message": "未找到大纲，请先生成大纲"}
        return
    script_prompt_system = await get_prompt_value("script-main", db, SCRIPT_AGENT_SYSTEM)

    total = len(outlines)
    for i, outline in enumerate(outlines):
        progress = int((i / total) * 85) + 5
        yield {
            "type": "progress", "stage": "script",
            "progress": progress,
            "message": f"正在生成第 {outline.episode_index} 集剧本（{i+1}/{total}）..."
        }

        # 构建剧本生成 prompt
        outline_data = outline.data
        prompt = f"""请根据以下大纲生成完整的第 {outline.episode_index} 集剧本《{outline.title}》：

剧情主干（outline）：
{outline_data.get('outline', '')}

开场钩子：{outline_data.get('openingHook', '')}

关键事件：
起：{outline_data.get('keyEvents', [''])[0] if outline_data.get('keyEvents') else ''}
承：{outline_data.get('keyEvents', ['',''])[1] if len(outline_data.get('keyEvents', [])) > 1 else ''}
转：{outline_data.get('keyEvents', ['','',''])[2] if len(outline_data.get('keyEvents', [])) > 2 else ''}
合：{outline_data.get('keyEvents', ['','','',''])[3] if len(outline_data.get('keyEvents', [])) > 3 else ''}

结尾悬念：{outline_data.get('endingHook', '')}

金句参考：{', '.join(outline_data.get('classicQuotes', []))}

出场角色：{', '.join([c.get('name','') for c in outline_data.get('characters', [])])}
主要场景：{', '.join([s.get('name','') for s in outline_data.get('scenes', [])])}

请生成完整剧本，格式规范，包含场景标注、动作描述和对白。"""

        messages = [{"role": "user", "content": prompt}]
        script_content = ""

        async for item in call_llm_stream(
            messages=messages,
            config_key="generateScript",
            db=db,
            user_id=user_id,
            system_prompt=script_prompt_system,
        ):
            if isinstance(item, dict) and item.get("type") == "fallback_warning":
                if item.get("reset_content"):
                    script_content = ""
                yield _as_stage_fallback_event("script", item)
                continue
            chunk = item
            script_content += chunk
            yield {"type": "content", "stage": "script", "data": {
                "episode_index": outline.episode_index, "chunk": chunk
            }}

        # 保存剧本
        existing = await db.execute(
            select(Script).where(Script.outline_id == outline.id)
        )
        script = existing.scalar_one_or_none()
        if script:
            script.content = script_content
            script.status = "done"
        else:
            script = Script(
                episode_index=outline.episode_index,
                title=outline.title,
                content=script_content,
                outline_id=outline.id,
                project_id=project_id,
                status="done",
            )
            db.add(script)
        await db.flush()

    yield {"type": "progress", "stage": "script", "progress": 95, "message": "所有集剧本生成完成"}
    yield {"type": "pause", "stage": "script", "message": "剧本生成完成，请查看并确认，或通过 Chat 进行修改"}


async def run_script_chat(
    project_id: int,
    message: str,
    db: AsyncSession,
    user_id: int,
    episode_index: Optional[int] = None,
) -> AsyncIterator[dict]:
    """剧本 Chat 修改模式"""
    query = select(Script).where(Script.project_id == project_id)
    if episode_index:
        query = query.where(Script.episode_index == episode_index)
    result = await db.execute(query.order_by(Script.episode_index))
    scripts = result.scalars().all()

    context = ""
    if episode_index and scripts:
        context = f"\n\n当前第{episode_index}集剧本内容（前1000字）：\n{scripts[0].content[:1000]}"

    script_chat_prompt = await get_prompt_value("script-chat", db, SCRIPT_CHAT_SYSTEM)
    system = script_chat_prompt + context
    messages = [{"role": "user", "content": message}]

    yield {"type": "progress", "stage": "script", "progress": 10, "message": "正在处理修改请求..."}

    async for item in call_llm_stream(
        messages=messages,
        config_key="generateScript",
        db=db,
        user_id=user_id,
        system_prompt=system,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            yield _as_stage_fallback_event("script", item)
            continue
        chunk = item
        yield {"type": "content", "stage": "script", "data": {"chunk": chunk}}

    yield {"type": "done", "stage": "script", "message": "修改完成"}

"""
大纲生成 Agent - LangGraph StateGraph
三节点流水线：故事线(AI1) → 大纲(AI2) → 导演审核(Director)
支持 Human-in-the-loop（interrupt），对应前端 PAUSED 状态
"""
import asyncio
import json
from typing import AsyncIterator, Optional, TypedDict, Annotated
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.services.llm import call_llm_stream, call_llm_structured
from app.services.prompt import get_prompt_value
from app.prompts.outline import STORYLINE_AGENT_SYSTEM, OUTLINE_AGENT_SYSTEM, DIRECTOR_AGENT_SYSTEM
from app.models.novel import Novel
from app.models.outline import Outline, Storyline


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


# ==================== State ====================

class OutlineAgentState(TypedDict):
    project_id: int
    messages: list[dict]           # 对话历史
    storyline: str                  # 故事线内容
    outlines: list[dict]            # 大纲数据列表
    director_feedback: str          # 导演审核意见
    current_stage: str              # storyline | outline | director | done
    episode_count: int              # 目标集数
    episode_duration: int           # 单集时长（分钟）
    user_confirmed: bool            # 用户是否已确认


# ==================== Tools（数据库操作） ====================

async def get_novel_chapters(project_id: int, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id).order_by(Novel.chapter_index)
    )
    chapters = result.scalars().all()
    return [
        {
            "index": c.chapter_index,
            "volume": c.volume,
            "title": c.chapter_title,
            "content": c.content,
        }
        for c in chapters
    ]


async def save_storyline(project_id: int, content: str, db: AsyncSession):
    result = await db.execute(select(Storyline).where(Storyline.project_id == project_id))
    storyline = result.scalar_one_or_none()
    if storyline:
        storyline.content = content
    else:
        storyline = Storyline(project_id=project_id, content=content)
        db.add(storyline)
    await db.flush()


async def save_outlines(project_id: int, episodes: list[dict], db: AsyncSession, overwrite: bool = True):
    if overwrite:
        # 删除现有大纲
        existing = await db.execute(select(Outline).where(Outline.project_id == project_id))
        for o in existing.scalars().all():
            await db.delete(o)
        await db.flush()

    for ep in episodes:
        outline = Outline(
            episode_index=ep["episodeIndex"],
            title=ep.get("title", ""),
            data=ep,
            project_id=project_id,
        )
        db.add(outline)
    await db.flush()


# ==================== Agent 运行（SSE 流式） ====================

async def run_outline_agent(
    project_id: int,
    db: AsyncSession,
    user_id: int,
    episode_count: int = 10,
    episode_duration: int = 1,
    chat_message: Optional[str] = None,  # 用于 Chat 修改模式
) -> AsyncIterator[dict]:
    """
    运行大纲生成 Agent，yield SSE 事件 dict
    """

    # 1. 获取章节
    yield {"type": "progress", "stage": "outline", "progress": 5, "message": "正在读取小说章节..."}
    chapters = await get_novel_chapters(project_id, db)
    if not chapters:
        yield {"type": "error", "stage": "outline", "message": "未找到小说章节，请先上传小说内容"}
        return

    chapter_text = "\n\n".join(
        [f"第{c['index']}章 {c['title'] or ''}\n{c['content'][:2000]}" for c in chapters[:20]]
    )
    storyline_prompt = await get_prompt_value("outlineScript-a1", db, STORYLINE_AGENT_SYSTEM)
    outline_prompt_system = await get_prompt_value("outlineScript-a2", db, OUTLINE_AGENT_SYSTEM)
    director_prompt = await get_prompt_value("outlineScript-director", db, DIRECTOR_AGENT_SYSTEM)

    # 2. AI1 - 生成故事线
    yield {"type": "progress", "stage": "outline", "progress": 15, "message": "故事师 AI1 正在分析原文，生成故事线..."}

    storyline_messages = [
        {"role": "user", "content": f"请分析以下小说原文，生成完整故事线：\n\n{chapter_text}"}
    ]

    storyline_content = ""
    async for item in call_llm_stream(
        messages=storyline_messages,
        config_key="outlineScriptAgent",
        db=db,
        user_id=user_id,
        system_prompt=storyline_prompt,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            if item.get("reset_content"):
                storyline_content = ""
            yield _as_stage_fallback_event("outline", item)
            continue
        chunk = item
        storyline_content += chunk
        yield {"type": "content", "stage": "outline", "data": {"node": "storyline", "chunk": chunk}}

    # 保存故事线
    await save_storyline(project_id, storyline_content, db)
    yield {"type": "progress", "stage": "outline", "progress": 35, "message": "故事线生成完成，保存成功"}

    # 3. AI2 - 生成大纲
    yield {"type": "progress", "stage": "outline", "progress": 45, "message": f"大纲师 AI2 正在生成 {episode_count} 集大纲..."}

    outline_prompt = f"""基于以下故事线，生成 {episode_count} 集短剧大纲，每集时长约 {episode_duration} 分钟。

故事线：
{storyline_content}

原文章节（参考）：
{chapter_text[:3000]}

请严格按照数据格式生成，包含 episodeIndex, title, chapterRange, scenes, characters, props,
coreConflict, outline, openingHook, keyEvents(4个元素数组), emotionalCurve, visualHighlights,
endingHook, classicQuotes。

生成后立即保存。"""

    outline_messages = [{"role": "user", "content": outline_prompt}]
    outline_content = ""
    async for item in call_llm_stream(
        messages=outline_messages,
        config_key="outlineScriptAgent",
        db=db,
        user_id=user_id,
        system_prompt=outline_prompt_system,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            if item.get("reset_content"):
                outline_content = ""
            yield _as_stage_fallback_event("outline", item)
            continue
        chunk = item
        outline_content += chunk
        yield {"type": "content", "stage": "outline", "data": {"node": "outline", "chunk": chunk}}

    yield {"type": "progress", "stage": "outline", "progress": 65, "message": "大纲生成完成"}

    # 4. Director - 审核
    yield {"type": "progress", "stage": "outline", "progress": 75, "message": "导演正在审核大纲质量..."}

    director_messages = [
        {"role": "user", "content": f"请审核以下大纲内容：\n\n{outline_content[:4000]}"}
    ]
    director_content = ""
    async for item in call_llm_stream(
        messages=director_messages,
        config_key="outlineScriptAgent",
        db=db,
        user_id=user_id,
        system_prompt=director_prompt,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            if item.get("reset_content"):
                director_content = ""
            yield _as_stage_fallback_event("outline", item)
            continue
        chunk = item
        director_content += chunk
        yield {"type": "content", "stage": "outline", "data": {"node": "director", "chunk": chunk}}

    yield {"type": "progress", "stage": "outline", "progress": 90, "message": "导演审核完成，等待用户确认"}

    # 5. Pause - 等待用户确认（Human-in-the-loop）
    yield {
        "type": "pause",
        "stage": "outline",
        "message": "大纲生成完成，导演已审核。请查看内容，确认通过后进入下一步，或通过 Chat 进行修改。",
        "data": {"director_feedback": director_content},
    }


async def run_outline_chat(
    project_id: int,
    message: str,
    db: AsyncSession,
    user_id: int,
) -> AsyncIterator[dict]:
    """
    大纲 Chat 优化模式 - 用户对大纲进行细节调整
    """
    # 获取当前大纲
    result = await db.execute(
        select(Outline).where(Outline.project_id == project_id).order_by(Outline.episode_index)
    )
    outlines = result.scalars().all()
    outlines_summary = "\n".join(
        [f"第{o.episode_index}集《{o.title}》：{o.data.get('outline', '')[:100]}" for o in outlines]
    )

    outline_prompt_system = await get_prompt_value("outlineScript-a2", db, OUTLINE_AGENT_SYSTEM)
    system = outline_prompt_system + f"\n\n当前项目已有 {len(outlines)} 集大纲：\n{outlines_summary}"
    messages = [{"role": "user", "content": message}]

    yield {"type": "progress", "stage": "outline", "progress": 10, "message": "正在处理你的修改请求..."}

    content = ""
    async for item in call_llm_stream(
        messages=messages,
        config_key="outlineScriptAgent",
        db=db,
        user_id=user_id,
        system_prompt=system,
    ):
        if isinstance(item, dict) and item.get("type") == "fallback_warning":
            if item.get("reset_content"):
                content = ""
            yield _as_stage_fallback_event("outline", item)
            continue
        chunk = item
        content += chunk
        yield {"type": "content", "stage": "outline", "data": {"chunk": chunk}}

    yield {"type": "done", "stage": "outline", "message": "修改完成"}

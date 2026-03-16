import json
import re
from datetime import datetime, timezone
from typing import AsyncIterator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.novel import BookEvaluation, Novel, NovelChatMessage, NovelEvaluation
from app.models.project import Project
from app.models.user import User
from app.schemas.novel import (
    BookEvaluationHistoryOut,
    BookEvaluationOut,
    NovelBatchCreate,
    NovelChatHistoryOut,
    NovelChatMessageOut,
    NovelChatRequest,
    NovelEvaluateBookRequest,
    NovelEvaluationOut,
    NovelEvaluateLiveRequest,
    NovelLatestEvaluationOut,
    NovelOut,
    NovelParseRequest,
    NovelReorderRequest,
    NovelStatsOut,
    NovelUpdate,
)
from app.services.novel_book_evaluator import NovelBookEvaluator
from app.services.novel_chat import recommend_chat_skill
from app.services.novel_evaluator import get_evaluator_by_content_type
from app.services.llm import call_llm_stream
from app.services.novel_parser import NovelParser

router = APIRouter(tags=["novel"])
novel_router = APIRouter(prefix="/projects/{project_id}/novels", tags=["novel"])
NOVEL_CHAT_SKILL_HINTS = {
    "chapter_eval": "按章节做精准评估，给出问题定位、分数解释和优先级建议。",
    "chapter_rewrite": "按用户目标改写指定章节，保持人设与主线一致，给出可直接替换的文本。",
    "story_overview": "提炼全书主线、分集节奏和结构风险，给出下一步优化路线。",
    "character_insight": "分析人物关系、动机和成长线，指出冲突与反转机会。",
    "platform_advice": "结合短剧发布平台给出内容包装、标题和节奏优化建议。",
}
NOVEL_CHAT_SKILL_PROMPTS = {
    "chapter_eval": (
        "你在本轮按章节评估模式工作。请优先给出："
        "1) 问题定位（引用章节号）"
        "2) 原因分析"
        "3) 可执行修改动作（按优先级 high/medium/low）。"
    ),
    "chapter_rewrite": (
        "你在本轮按章节改写模式工作。请按“改写意图 -> 可替换正文 -> 修改说明”输出，"
        "并保持人物设定、叙事视角和主线因果不漂移。"
    ),
    "story_overview": (
        "你在本轮全书梳理模式工作。请给出“主线摘要 -> 分集节奏 -> 结构风险 -> 下一步优化路线”，"
        "并明确建议应落到哪些章节。"
    ),
    "character_insight": (
        "你在本轮人物分析模式工作。请输出“角色目标/阻碍/转变 -> 关系张力 -> 可做冲突与反转点”，"
        "避免泛泛分析。"
    ),
    "platform_advice": (
        "你在本轮平台建议模式工作。请结合内容类型与当前文本，给出“目标平台画像 -> 标题包装 -> 开篇节奏优化"
        " -> 分集长度/挂念建议”。"
    ),
}
NOVEL_CHAT_HISTORY_LIMIT = 12

NOVEL_CHAT_SYSTEM_PROMPT = """你是小说改编与内容诊断顾问，目标是帮助用户高效改进当前项目的章节。

输出要求：
1. 回答必须可执行，优先给具体章节、具体改法、具体理由。
2. 涉及“评估”时，请按“问题 -> 原因 -> 建议”格式组织。
3. 涉及“改写”时，请直接给可替换正文，并说明改写意图。
4. 当用户选择了章节范围，只围绕这些章节回答；若未指定，则可先概览再给聚焦建议。
5. 不编造不存在的章节内容；信息不足时明确说明并给下一步输入建议。
"""


def _count_words(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    return len(compact)


def _serialize_chat_message(message: NovelChatMessage) -> NovelChatMessageOut:
    role = message.role if message.role in {"user", "assistant"} else "assistant"
    skill = message.skill if message.skill in NOVEL_CHAT_SKILL_HINTS else None
    novel_ids = message.selected_novel_ids or []
    if not isinstance(novel_ids, list):
        novel_ids = []
    return NovelChatMessageOut(
        id=message.id,
        role=role,
        message=message.message,
        skill=skill,
        novel_ids=[int(item) for item in novel_ids if isinstance(item, int)],
        created_at=message.created_at,
    )


async def _get_user_project(project_id: int, user: User, db: AsyncSession) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


async def _get_latest_evaluation(
    novel_id: int,
    project_id: int,
    db: AsyncSession,
) -> NovelEvaluation | None:
    result = await db.execute(
        select(NovelEvaluation)
        .where(NovelEvaluation.project_id == project_id, NovelEvaluation.novel_id == novel_id)
        .order_by(NovelEvaluation.created_at.desc(), NovelEvaluation.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _build_evaluation_record(
    *,
    novel: Novel,
    project: Project,
    evaluation_data: dict,
    previous: NovelEvaluation | None,
) -> NovelEvaluation:
    return NovelEvaluation(
        novel_id=novel.id,
        content_type=project.content_type,
        evaluation_type="chapter_only",
        overall_score=evaluation_data["overall_score"],
        dimension_scores=evaluation_data["dimension_scores"],
        summary=evaluation_data["summary"],
        suggestions=evaluation_data["suggestions"],
        novel_revision=(previous.novel_revision + 1) if previous else 1,
        parent_evaluation_id=previous.id if previous else None,
        model_used=evaluation_data.get("model_used", "novel_evaluator"),
        prompt_version=evaluation_data.get("prompt_version", f"{project.content_type}.v1"),
        project_id=project.id,
    )


def _serialize_evaluation(evaluation: NovelEvaluation) -> dict:
    return {
        "id": evaluation.id,
        "novel_id": evaluation.novel_id,
        "content_type": evaluation.content_type,
        "evaluation_type": evaluation.evaluation_type,
        "overall_score": evaluation.overall_score,
        "dimension_scores": evaluation.dimension_scores,
        "summary": evaluation.summary,
        "suggestions": evaluation.suggestions,
        "novel_revision": evaluation.novel_revision,
        "parent_evaluation_id": evaluation.parent_evaluation_id,
        "model_used": evaluation.model_used,
        "prompt_version": evaluation.prompt_version,
        "project_id": evaluation.project_id,
        "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
        "updated_at": evaluation.updated_at.isoformat() if evaluation.updated_at else None,
    }


def _serialize_book_evaluation(evaluation: BookEvaluation) -> dict:
    return {
        "id": evaluation.id,
        "project_id": evaluation.project_id,
        "content_type": evaluation.content_type,
        "evaluated_novel_ids": evaluation.evaluated_novel_ids or [],
        "aggregated_stats": evaluation.aggregated_stats or {},
        "consistency_issues": evaluation.consistency_issues or [],
        "overall_assessment": evaluation.overall_assessment or {},
        "model_used": evaluation.model_used,
        "prompt_version": evaluation.prompt_version,
        "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
        "updated_at": evaluation.updated_at.isoformat() if evaluation.updated_at else None,
    }


def _make_sse(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False)
    if event.get("type") == "fallback_warning":
        return f"event: fallback_warning\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def _stream_response(gen: AsyncIterator[dict]) -> StreamingResponse:
    async def _stream():
        async for event in gen:
            yield _make_sse(event)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@novel_router.get("", response_model=List[NovelOut])
async def list_novels(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)
    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id).order_by(Novel.chapter_index)
    )
    return result.scalars().all()


@novel_router.get("/stats", response_model=NovelStatsOut)
async def get_novel_stats(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    volume_expr = func.coalesce(func.nullif(Novel.volume, ""), "正文")
    stats_result = await db.execute(
        select(
            func.count(Novel.id),
            func.coalesce(func.sum(Novel.word_count), 0),
            func.count(func.distinct(volume_expr)),
        ).where(Novel.project_id == project_id)
    )
    total_chapters, total_words, total_volumes = stats_result.one()

    ranked_eval = (
        select(
            NovelEvaluation.id.label("id"),
            NovelEvaluation.novel_id.label("novel_id"),
            func.row_number()
            .over(
                partition_by=NovelEvaluation.novel_id,
                order_by=(NovelEvaluation.created_at.desc(), NovelEvaluation.id.desc()),
            )
            .label("rn"),
        )
        .where(NovelEvaluation.project_id == project_id)
        .subquery()
    )
    avg_result = await db.execute(
        select(func.avg(NovelEvaluation.overall_score))
        .join(ranked_eval, ranked_eval.c.id == NovelEvaluation.id)
        .where(ranked_eval.c.rn == 1)
    )
    avg_score = avg_result.scalar_one_or_none()

    return NovelStatsOut(
        total_chapters=int(total_chapters or 0),
        total_words=int(total_words or 0),
        total_volumes=int(total_volumes or 0),
        average_score=round(float(avg_score), 2) if avg_score is not None else None,
    )


@novel_router.post("")
async def create_novels(
    project_id: int,
    body: NovelBatchCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量创建章节。"""
    await _get_user_project(project_id, user, db)

    if not body.chapters:
        raise HTTPException(status_code=400, detail="chapters 不能为空")

    indices = [chapter.chapter_index for chapter in body.chapters]
    if len(indices) != len(set(indices)):
        raise HTTPException(status_code=400, detail="请求中 chapter_index 不能重复")

    result = await db.execute(
        select(Novel.chapter_index).where(
            Novel.project_id == project_id,
            Novel.chapter_index.in_(indices),
        )
    )
    duplicated = sorted(set(result.scalars().all()))
    if duplicated:
        raise HTTPException(status_code=409, detail=f"chapter_index 已存在：{duplicated}")

    for chapter in body.chapters:
        content = chapter.content.strip()
        novel = Novel(
            chapter_index=chapter.chapter_index,
            volume=chapter.volume,
            chapter_title=chapter.chapter_title,
            content=content,
            word_count=_count_words(content),
            project_id=project_id,
        )
        db.add(novel)

    await db.flush()
    return {"code": 0, "msg": f"已上传 {len(body.chapters)} 章节"}


@novel_router.put("/reorder")
async def reorder_novels(
    project_id: int,
    body: NovelReorderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    if not body.orders:
        raise HTTPException(status_code=400, detail="orders 不能为空")

    ids = [item.novel_id for item in body.orders]
    chapter_indices = [item.chapter_index for item in body.orders]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="novel_id 不能重复")
    if len(chapter_indices) != len(set(chapter_indices)):
        raise HTTPException(status_code=400, detail="chapter_index 不能重复")

    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id, Novel.id.in_(ids))
    )
    novels = result.scalars().all()
    if len(novels) != len(ids):
        raise HTTPException(status_code=404, detail="存在无效章节 ID")
    total_count = await db.scalar(
        select(func.count(Novel.id)).where(Novel.project_id == project_id)
    )
    if int(total_count or 0) != len(ids):
        raise HTTPException(status_code=400, detail="reorder 需要提交项目全部章节顺序")

    chapter_index_map = {item.novel_id: item.chapter_index for item in body.orders}
    for novel in novels:
        novel.chapter_index = chapter_index_map[novel.id]

    await db.flush()
    return {"code": 0, "msg": "排序已更新"}


@novel_router.post("/parse")
async def parse_novel(
    project_id: int,
    body: NovelParseRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)
    parser = NovelParser()

    async def stream():
        async for event in parser.parse(
            raw_text=body.raw_text,
            mode=body.mode,
            db=db,
            user_id=user.id,
            options=body.model_dump(exclude={"raw_text", "mode"}, exclude_none=True),
        ):
            yield event

    return _stream_response(stream())


@novel_router.get("/chat/history", response_model=NovelChatHistoryOut)
async def list_chat_history(
    project_id: int,
    limit: int = 80,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit 取值范围为 1-200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 不能小于 0")

    total = await db.scalar(
        select(func.count(NovelChatMessage.id)).where(
            NovelChatMessage.project_id == project_id,
            NovelChatMessage.user_id == user.id,
        )
    )
    result = await db.execute(
        select(NovelChatMessage)
        .where(
            NovelChatMessage.project_id == project_id,
            NovelChatMessage.user_id == user.id,
        )
        .order_by(NovelChatMessage.created_at.desc(), NovelChatMessage.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(reversed(result.scalars().all()))
    return NovelChatHistoryOut(
        total=int(total or 0),
        messages=[_serialize_chat_message(item) for item in rows],
    )


@novel_router.delete("/chat/history")
async def clear_chat_history(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)
    await db.execute(
        delete(NovelChatMessage).where(
            NovelChatMessage.project_id == project_id,
            NovelChatMessage.user_id == user.id,
        )
    )
    await db.commit()
    return {"code": 0, "msg": "小说 Chat 历史已清空"}


@novel_router.post("/chat")
async def chat_novel(
    project_id: int,
    body: NovelChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id).order_by(Novel.chapter_index)
    )
    novels = result.scalars().all()
    if not novels:
        raise HTTPException(status_code=400, detail="当前项目暂无章节，无法进行小说 Chat")

    selected_novels = novels
    selected_novel_ids = body.novel_ids or []
    if body.novel_ids:
        selected_ids = set(body.novel_ids)
        selected_novels = [item for item in novels if item.id in selected_ids]
        if len(selected_novels) != len(selected_ids):
            raise HTTPException(status_code=404, detail="novel_ids 中存在无效章节")

    recent_chat_result = await db.execute(
        select(NovelChatMessage)
        .where(
            NovelChatMessage.project_id == project_id,
            NovelChatMessage.user_id == user.id,
        )
        .order_by(NovelChatMessage.created_at.desc(), NovelChatMessage.id.desc())
        .limit(NOVEL_CHAT_HISTORY_LIMIT)
    )
    recent_chat_rows = list(reversed(recent_chat_result.scalars().all()))
    history_messages = [
        {"role": item.role, "content": item.message}
        for item in recent_chat_rows
        if item.role in {"user", "assistant"} and (item.message or "").strip()
    ]

    effective_skill = body.skill
    recommended_reason: str | None = None
    if not effective_skill:
        effective_skill, recommended_reason = recommend_chat_skill(body.message)

    chapter_lines = [
        f"- 第{item.chapter_index}章《{item.chapter_title or f'第{item.chapter_index}章'}》"
        f"（ID:{item.id}，{int(item.word_count or 0)}字）"
        for item in novels
    ]
    selected_preview = "\n".join(
        [
            f"### 第{item.chapter_index}章《{item.chapter_title or f'第{item.chapter_index}章'}》\n"
            f"{(item.content or '').strip()[:1800]}"
            for item in selected_novels[:6]
        ]
    )
    skill_hint = NOVEL_CHAT_SKILL_HINTS.get(effective_skill or "")
    skill_prompt = NOVEL_CHAT_SKILL_PROMPTS.get(effective_skill or "")
    evaluation_briefs = []
    for item in selected_novels[:8]:
        latest = await _get_latest_evaluation(item.id, project_id, db)
        if not latest:
            continue
        evaluation_briefs.append(
            f"- 第{item.chapter_index}章：总分{round(float(latest.overall_score), 2)}，"
            f"关键建议数 {len(latest.suggestions or [])}"
        )

    system_parts = [
        NOVEL_CHAT_SYSTEM_PROMPT.strip(),
        f"当前项目内容类型：{project.content_type}",
        f"全书章节数：{len(novels)}",
        "全书章节索引：\n" + "\n".join(chapter_lines[:60]),
        f"当前会话聚焦章节 ID：{selected_novel_ids or '未指定（全书视角）'}",
        "聚焦章节正文（节选）：\n" + (selected_preview or "无"),
    ]
    if skill_hint:
        system_parts.append(f"本次技能目标：{skill_hint}")
    if skill_prompt:
        system_parts.append(f"技能执行要求：{skill_prompt}")
    if evaluation_briefs:
        system_parts.append("聚焦章节已有评估摘要：\n" + "\n".join(evaluation_briefs))

    async def stream():
        user_record = NovelChatMessage(
            project_id=project_id,
            user_id=user.id,
            role="user",
            message=body.message,
            skill=effective_skill,
            selected_novel_ids=selected_novel_ids,
        )
        db.add(user_record)
        await db.flush()
        await db.commit()

        if recommended_reason and effective_skill:
            yield {
                "type": "skill_recommendation",
                "recommended_skill": effective_skill,
                "reason": recommended_reason,
            }
        yield {"type": "progress", "message": "正在分析你的请求...", "progress": 8}
        assistant_chunks: list[str] = []
        async for item in call_llm_stream(
            messages=[*history_messages, {"role": "user", "content": body.message}],
            config_key="novel_evaluator",
            db=db,
            user_id=user.id,
            system_prompt="\n\n".join(system_parts),
        ):
            if isinstance(item, dict) and item.get("type") == "fallback_warning":
                yield item
                continue
            chunk = str(item or "")
            if not chunk:
                continue
            assistant_chunks.append(chunk)
            yield {"type": "content", "data": {"chunk": chunk}}

        assistant_message = "".join(assistant_chunks).strip()
        if assistant_message:
            assistant_record = NovelChatMessage(
                project_id=project_id,
                user_id=user.id,
                role="assistant",
                message=assistant_message,
                skill=effective_skill,
                selected_novel_ids=selected_novel_ids,
            )
            db.add(assistant_record)
            await db.flush()
            await db.commit()
        yield {"type": "done", "message": "已完成本轮小说 Chat", "skill": effective_skill}

    return _stream_response(stream())


@novel_router.post("/{novel_id}/evaluate-live")
async def evaluate_novel_live(
    project_id: int,
    novel_id: int,
    body: NovelEvaluateLiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.id == novel_id, Novel.project_id == project_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="章节不存在")

    evaluator = get_evaluator_by_content_type(project.content_type)
    try:
        live_result = await evaluator.evaluate_live(
            temporary_content=body.temporary_content,
            chapter_title=body.chapter_title or novel.chapter_title,
            db=db,
            user_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"实时评估失败: {exc}") from exc

    return {
        **live_result,
        "novel_id": novel.id,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


@novel_router.post("/evaluate-book", response_model=BookEvaluationOut)
async def evaluate_book(
    project_id: int,
    body: NovelEvaluateBookRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id).order_by(Novel.chapter_index)
    )
    novels = result.scalars().all()
    if not novels:
        raise HTTPException(status_code=400, detail="当前项目没有章节，无法执行全书评估")

    if body.novel_ids and body.chapters_to_evaluate:
        raise HTTPException(status_code=400, detail="novel_ids 与 chapters_to_evaluate 不能同时传入")

    if body.novel_ids:
        novel_id_set = set(body.novel_ids)
        selected_novels = [item for item in novels if item.id in novel_id_set]
        if len(selected_novels) != len(novel_id_set):
            raise HTTPException(status_code=404, detail="novel_ids 中包含无效章节")
    elif body.chapters_to_evaluate:
        chapter_index_set = set(body.chapters_to_evaluate)
        selected_novels = [item for item in novels if item.chapter_index in chapter_index_set]
        if len(selected_novels) != len(chapter_index_set):
            raise HTTPException(status_code=404, detail="chapters_to_evaluate 中包含无效章节序号")
    else:
        selected_novels = novels

    if body.force_re_evaluate:
        raise HTTPException(status_code=400, detail="已取消自动重评，请先逐章评估后再生成全书仪表盘")

    latest_map: dict[int, NovelEvaluation] = {}
    missing_chapters: list[int] = []
    for novel in selected_novels:
        latest = await _get_latest_evaluation(novel.id, project_id, db)
        if latest:
            latest_map[novel.id] = latest
        else:
            missing_chapters.append(novel.chapter_index)

    if missing_chapters:
        chapter_list = "、".join([f"第{idx}章" for idx in sorted(missing_chapters)])
        raise HTTPException(
            status_code=400,
            detail=f"以下章节缺少评估结果，请先完成章节评估：{chapter_list}",
        )

    selected_evaluations = [latest_map[item.id] for item in selected_novels]

    book_evaluator = NovelBookEvaluator(content_type=project.content_type)
    report = book_evaluator.build_report(
        novels=selected_novels,
        evaluations=selected_evaluations,
        focus_areas=set(body.focus_areas or []),
        include_benchmarking=body.include_benchmarking,
    )

    record = BookEvaluation(
        project_id=project_id,
        content_type=project.content_type,
        evaluated_novel_ids=report["evaluated_novel_ids"],
        aggregated_stats=report["aggregated_stats"],
        consistency_issues=report["consistency_issues"],
        overall_assessment=report["overall_assessment"],
        model_used=report["model_used"],
        prompt_version=report["prompt_version"],
    )
    db.add(record)
    await db.flush()
    await db.commit()
    await db.refresh(record)
    return BookEvaluationOut.model_validate(record)


@novel_router.get("/book/history", response_model=BookEvaluationHistoryOut)
async def list_book_evaluation_history(
    project_id: int,
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    if limit <= 0 or limit > 50:
        raise HTTPException(status_code=400, detail="limit 取值范围为 1-50")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 不能小于 0")

    total = await db.scalar(
        select(func.count(BookEvaluation.id)).where(BookEvaluation.project_id == project_id)
    )
    result = await db.execute(
        select(BookEvaluation)
        .where(BookEvaluation.project_id == project_id)
        .order_by(BookEvaluation.created_at.desc(), BookEvaluation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return BookEvaluationHistoryOut(
        total=int(total or 0),
        evaluations=[BookEvaluationOut.model_validate(row) for row in rows],
    )


@novel_router.get("/{novel_id}/evaluations", response_model=List[NovelEvaluationOut])
async def list_novel_evaluations(
    project_id: int,
    novel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    novel_result = await db.execute(
        select(Novel).where(Novel.id == novel_id, Novel.project_id == project_id)
    )
    novel = novel_result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="章节不存在")

    result = await db.execute(
        select(NovelEvaluation)
        .where(NovelEvaluation.project_id == project_id, NovelEvaluation.novel_id == novel_id)
        .order_by(NovelEvaluation.created_at.desc(), NovelEvaluation.id.desc())
    )
    return result.scalars().all()


@novel_router.post("/{novel_id}/evaluate")
async def evaluate_novel(
    project_id: int,
    novel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.id == novel_id, Novel.project_id == project_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="章节不存在")

    evaluator = get_evaluator_by_content_type(project.content_type)

    async def stream():
        yield {"type": "progress", "message": "正在分析章节内容...", "progress": 15}

        try:
            evaluation_data, fallback_events = await evaluator.evaluate_single(
                novel=novel,
                db=db,
                user_id=user.id,
            )
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        for event in fallback_events:
            yield event

        for index, dimension in enumerate(evaluator.dimensions, start=1):
            score = evaluation_data["dimension_scores"].get(dimension)
            if score is None:
                continue
            yield {
                "type": "dimension",
                "name": dimension,
                "score": score,
                "progress": 20 + int(index / len(evaluator.dimensions) * 60),
            }

        previous = await _get_latest_evaluation(novel.id, project_id, db)
        evaluation = _build_evaluation_record(
            novel=novel,
            project=project,
            evaluation_data=evaluation_data,
            previous=previous,
        )
        db.add(evaluation)
        await db.flush()
        await db.commit()

        yield {
            "type": "done",
            "evaluation_id": evaluation.id,
            "overall_score": evaluation_data["overall_score"],
            "evaluation": _serialize_evaluation(evaluation),
        }

    return _stream_response(stream())


@novel_router.put("/{novel_id}", response_model=NovelOut)
async def update_novel(
    project_id: int,
    novel_id: int,
    body: NovelUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.id == novel_id, Novel.project_id == project_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="章节不存在")

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="未提供需要更新的字段")

    if "chapter_index" in payload and payload["chapter_index"] != novel.chapter_index:
        conflict = await db.execute(
            select(Novel.id).where(
                Novel.project_id == project_id,
                Novel.chapter_index == payload["chapter_index"],
                Novel.id != novel_id,
            )
        )
        if conflict.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="chapter_index 已存在")

    for key, value in payload.items():
        setattr(novel, key, value)

    if "content" in payload and novel.content:
        novel.word_count = _count_words(novel.content)

    await db.flush()
    await db.refresh(novel)
    return novel


@novel_router.delete("/{novel_id}")
async def delete_novel(
    project_id: int,
    novel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    result = await db.execute(
        select(Novel).where(Novel.id == novel_id, Novel.project_id == project_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="章节不存在")
    await db.delete(novel)
    return {"code": 0, "msg": "已删除"}


@novel_router.delete("")
async def delete_all_novels(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    await db.execute(delete(Novel).where(Novel.project_id == project_id))
    return {"code": 0, "msg": "已清空所有章节"}


@router.get("/projects/{project_id}/evaluations/latest", response_model=List[NovelLatestEvaluationOut])
async def list_latest_evaluations(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    ranked_eval = (
        select(
            NovelEvaluation.id.label("id"),
            NovelEvaluation.novel_id.label("novel_id"),
            func.row_number()
            .over(
                partition_by=NovelEvaluation.novel_id,
                order_by=(NovelEvaluation.created_at.desc(), NovelEvaluation.id.desc()),
            )
            .label("rn"),
        )
        .where(NovelEvaluation.project_id == project_id)
        .subquery()
    )

    result = await db.execute(
        select(NovelEvaluation)
        .join(ranked_eval, and_(ranked_eval.c.id == NovelEvaluation.id, ranked_eval.c.rn == 1))
        .order_by(NovelEvaluation.novel_id.asc())
    )
    rows = result.scalars().all()

    return [
        NovelLatestEvaluationOut(
            novel_id=row.novel_id,
            evaluation=NovelEvaluationOut.model_validate(row),
        )
        for row in rows
    ]


router.include_router(novel_router)

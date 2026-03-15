import json
import re
from datetime import datetime
from typing import AsyncIterator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.novel import Novel, NovelEvaluation
from app.models.project import Project
from app.models.user import User
from app.schemas.novel import (
    NovelBatchCreate,
    NovelEvaluateBatchRequest,
    NovelEvaluationOut,
    NovelEvaluateLiveRequest,
    NovelLatestEvaluationOut,
    NovelOut,
    NovelParseRequest,
    NovelReorderRequest,
    NovelStatsOut,
    NovelUpdate,
)
from app.services.novel_evaluator import get_evaluator_by_content_type
from app.services.novel_parser import NovelParser

router = APIRouter(tags=["novel"])
novel_router = APIRouter(prefix="/projects/{project_id}/novels", tags=["novel"])


def _count_words(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    return len(compact)


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
        "generated_at": datetime.utcnow().isoformat(),
    }


@novel_router.post("/evaluate-all")
async def evaluate_all_novels(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)
    evaluator = get_evaluator_by_content_type(project.content_type)

    result = await db.execute(
        select(Novel).where(Novel.project_id == project_id).order_by(Novel.chapter_index)
    )
    novels = result.scalars().all()

    async def stream():
        if not novels:
            yield {"type": "error", "message": "当前项目没有章节可评估"}
            return

        chapter_scores: list[tuple[int, float]] = []
        for idx, novel in enumerate(novels, start=1):
            title = novel.chapter_title or f"第{novel.chapter_index}章"
            yield {
                "type": "chapter_start",
                "novel_id": novel.id,
                "chapter_title": title,
                "index": idx,
                "total": len(novels),
            }
            try:
                evaluation_data, fallback_events = await evaluator.evaluate_single(
                    novel=novel,
                    db=db,
                    user_id=user.id,
                )
            except Exception as exc:
                yield {
                    "type": "chapter_done",
                    "novel_id": novel.id,
                    "chapter_title": title,
                    "error": str(exc),
                }
                continue

            for event in fallback_events:
                yield event

            previous = await _get_latest_evaluation(novel.id, project_id, db)
            evaluation = _build_evaluation_record(
                novel=novel,
                project=project,
                evaluation_data=evaluation_data,
                previous=previous,
            )
            db.add(evaluation)
            await db.flush()

            chapter_scores.append((novel.id, evaluation_data["overall_score"]))
            yield {
                "type": "chapter_done",
                "novel_id": novel.id,
                "chapter_title": title,
                "overall_score": evaluation_data["overall_score"],
            }

        await db.commit()

        if not chapter_scores:
            yield {"type": "done", "avg_score": None, "best_chapter": None, "weakest_chapter": None}
            return

        avg_score = round(sum(score for _, score in chapter_scores) / len(chapter_scores), 2)
        best_chapter = max(chapter_scores, key=lambda x: x[1])[0]
        weakest_chapter = min(chapter_scores, key=lambda x: x[1])[0]

        yield {
            "type": "done",
            "avg_score": avg_score,
            "best_chapter": best_chapter,
            "weakest_chapter": weakest_chapter,
        }

    return _stream_response(stream())


@novel_router.post("/evaluate-batch")
async def evaluate_batch_novels(
    project_id: int,
    body: NovelEvaluateBatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_user_project(project_id, user, db)
    evaluator = get_evaluator_by_content_type(project.content_type)

    result = await db.execute(
        select(Novel)
        .where(Novel.project_id == project_id, Novel.id.in_(body.novel_ids))
        .order_by(Novel.chapter_index)
    )
    novels = result.scalars().all()
    if len(novels) != len(body.novel_ids):
        raise HTTPException(status_code=404, detail="存在无效章节 ID")

    async def stream():
        results: list[dict] = []
        for idx, novel in enumerate(novels, start=1):
            title = novel.chapter_title or f"第{novel.chapter_index}章"
            yield {
                "type": "progress",
                "status": "processing",
                "current": idx,
                "total": len(novels),
                "novel_id": novel.id,
                "chapter": title,
            }

            try:
                evaluation_data, fallback_events = await evaluator.evaluate_single(
                    novel=novel,
                    db=db,
                    user_id=user.id,
                )
            except Exception as exc:
                results.append(
                    {
                        "novel_id": novel.id,
                        "chapter_title": title,
                        "error": str(exc),
                    }
                )
                continue

            for event in fallback_events:
                yield event

            previous = await _get_latest_evaluation(novel.id, project_id, db)
            evaluation = _build_evaluation_record(
                novel=novel,
                project=project,
                evaluation_data=evaluation_data,
                previous=previous,
            )
            db.add(evaluation)
            await db.flush()

            results.append(
                {
                    "novel_id": novel.id,
                    "chapter_title": title,
                    "overall_score": evaluation.overall_score,
                    "evaluation": _serialize_evaluation(evaluation),
                }
            )

        await db.commit()
        yield {
            "type": "complete",
            "total": len(novels),
            "results": results,
        }

    return _stream_response(stream())


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


@novel_router.get("/{novel_id}/evaluations/compare")
async def compare_novel_evaluations(
    project_id: int,
    novel_id: int,
    version1: int,
    version2: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_project(project_id, user, db)

    def _normalize_suggestion_dimensions(raw_suggestions: list) -> set[str]:
        dimensions: set[str] = set()
        for item in raw_suggestions or []:
            if isinstance(item, dict):
                dim = str(item.get("dimension") or "").strip()
                if dim:
                    dimensions.add(dim)
        return dimensions

    result = await db.execute(
        select(NovelEvaluation).where(
            NovelEvaluation.project_id == project_id,
            NovelEvaluation.novel_id == novel_id,
            NovelEvaluation.id.in_([version1, version2]),
        )
    )
    rows = result.scalars().all()
    if len(rows) != 2:
        raise HTTPException(status_code=404, detail="评估版本不存在")

    by_id = {item.id: item for item in rows}
    eval1 = by_id.get(version1)
    eval2 = by_id.get(version2)
    if not eval1 or not eval2:
        raise HTTPException(status_code=404, detail="评估版本不存在")

    all_dimensions = set((eval1.dimension_scores or {}).keys()) | set((eval2.dimension_scores or {}).keys())
    comparison: dict[str, dict] = {}
    for key in sorted(all_dimensions):
        before = float((eval1.dimension_scores or {}).get(key, 0))
        after = float((eval2.dimension_scores or {}).get(key, 0))
        comparison[key] = {
            "before": round(before, 2),
            "after": round(after, 2),
            "delta": round(after - before, 2),
        }

    dim1 = _normalize_suggestion_dimensions(eval1.suggestions)
    dim2 = _normalize_suggestion_dimensions(eval2.suggestions)

    return {
        "version1": _serialize_evaluation(eval1),
        "version2": _serialize_evaluation(eval2),
        "comparison": comparison,
        "suggestions_resolved": len(dim1 - dim2),
        "new_issues": len(dim2 - dim1),
    }


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

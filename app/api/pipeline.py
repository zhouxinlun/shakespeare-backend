"""
Pipeline API - 状态机核心端点
每个阶段：触发生成（SSE）、Chat 优化（SSE）、确认通过、重置
"""
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.project import Project
from app.models.novel import Novel, NovelEvaluation
from app.models.outline import Outline, Storyline
from app.models.script import Script
from app.models.storyboard import Storyboard
from app.schemas.pipeline import StageStatus, PipelineState, STAGE_DEPS, CHAT_ENABLED_STAGES

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
STAGE_ORDER = ["novel", "outline", "script", "storyboard", "images", "video"]


# ==================== 状态机辅助函数 ====================

async def get_project_or_404(project_id: int, user: User, db: AsyncSession) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


async def update_pipeline_state(
    project_id: int,
    db: AsyncSession,
    **kwargs
):
    """更新 pipeline_state 字段（JSONB merge）"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return
    state = dict(project.pipeline_state or {})
    state.update(kwargs)
    project.pipeline_state = state
    await db.flush()


async def get_pipeline_state(project_id: int, db: AsyncSession) -> PipelineState | None:
    result = await db.execute(select(Project.pipeline_state).where(Project.id == project_id))
    raw_state = result.scalar_one_or_none()
    if raw_state is None:
        return None
    return PipelineState(**raw_state)


async def is_stage_cancelled(project_id: int, stage: str, db: AsyncSession) -> bool:
    state = await get_pipeline_state(project_id, db)
    if not state:
        return False
    return getattr(state, stage, None) == StageStatus.CANCELLED


async def clear_stage_data(project_id: int, stage: str, db: AsyncSession) -> None:
    stage_index = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1
    stages_to_clear = STAGE_ORDER[stage_index:] if stage_index >= 0 else [stage]

    if "storyboard" in stages_to_clear:
        await db.execute(delete(Storyboard).where(Storyboard.project_id == project_id))
    if "script" in stages_to_clear:
        await db.execute(delete(Script).where(Script.project_id == project_id))
    if "outline" in stages_to_clear:
        await db.execute(delete(Storyline).where(Storyline.project_id == project_id))
        await db.execute(delete(Outline).where(Outline.project_id == project_id))
    if "novel" in stages_to_clear:
        await db.execute(delete(NovelEvaluation).where(NovelEvaluation.project_id == project_id))
        await db.execute(delete(Novel).where(Novel.project_id == project_id))


def make_sse(event: dict) -> str:
    """格式化 SSE 数据帧"""
    payload = json.dumps(event, ensure_ascii=False)
    if event.get("type") == "fallback_warning":
        return f"event: fallback_warning\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


async def sse_generator(project_id: int, stage: str, agent_gen, db: AsyncSession):
    """
    通用 SSE 生成器：包装 agent 的 AsyncIterator，同步更新 DB 状态
    """
    # 标记为 RUNNING
    await update_pipeline_state(project_id, db, **{stage: StageStatus.RUNNING, "current_stage": stage, "current_progress": 0})
    await db.commit()

    try:
        async for event in agent_gen:
            if await is_stage_cancelled(project_id, stage, db):
                yield make_sse(
                    {
                        "type": "state_change",
                        "stage": stage,
                        "status": StageStatus.CANCELLED,
                        "message": "任务已取消",
                    }
                )
                yield make_sse(
                    {
                        "type": "done",
                        "stage": stage,
                        "message": "任务已取消",
                    }
                )
                return
            # 同步更新进度到 DB
            if event.get("type") == "progress":
                await update_pipeline_state(
                    project_id, db,
                    current_progress=event.get("progress", 0),
                    current_message=event.get("message", ""),
                )
                await db.commit()
            elif event.get("type") == "pause":
                await update_pipeline_state(
                    project_id, db,
                    **{stage: StageStatus.PAUSED},
                    current_progress=90,
                    current_message=event.get("message", "等待用户确认"),
                )
                await db.commit()
            elif event.get("type") == "error":
                await update_pipeline_state(
                    project_id, db,
                    **{stage: StageStatus.FAILED},
                    error=event.get("message", "未知错误"),
                    current_stage=None,
                )
                await db.commit()
            elif event.get("type") == "done":
                await update_pipeline_state(
                    project_id,
                    db,
                    current_stage=None,
                    current_message=event.get("message", ""),
                )
                await db.commit()

            yield make_sse(event)

    except Exception as e:
        await update_pipeline_state(
            project_id, db,
            **{stage: StageStatus.FAILED},
            error=str(e),
            current_stage=None,
        )
        await db.commit()
        yield make_sse({"type": "error", "stage": stage, "message": str(e)})


# ==================== 端点 ====================

@router.post("/{project_id}/run/{stage}")
async def run_stage(
    project_id: int,
    stage: str,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    触发某个阶段的生成（返回 SSE 流）
    """
    project = await get_project_or_404(project_id, user, db)
    pipeline = PipelineState(**project.pipeline_state)

    # 检查前置依赖
    dep = STAGE_DEPS.get(stage)
    if dep:
        dep_status = getattr(pipeline, dep)
        if dep_status not in (StageStatus.DONE, StageStatus.SKIPPED):
            raise HTTPException(status_code=400, detail=f"请先完成「{dep}」阶段")

    # 检查当前是否在运行
    current_status = getattr(pipeline, stage, None)
    if current_status == StageStatus.RUNNING:
        raise HTTPException(status_code=400, detail="该阶段正在运行中")

    # 根据 stage 选择对应的 agent
    if stage == "outline":
        from app.agents.outline_agent import run_outline_agent
        episode_count = body.get("episode_count", 10)
        episode_duration = body.get("episode_duration", 1)
        agent_gen = run_outline_agent(project_id, db, user.id, episode_count, episode_duration)

    elif stage == "script":
        from app.agents.script_agent import run_script_agent
        agent_gen = run_script_agent(project_id, db, user.id)

    elif stage == "storyboard":
        from app.agents.storyboard_agent import run_storyboard_agent
        agent_gen = run_storyboard_agent(project_id, db, user.id)

    elif stage == "novel":
        # novel 阶段只需确认章节已上传，直接标记 DONE
        async def novel_check():
            from app.models.novel import Novel
            result = await db.execute(select(Novel).where(Novel.project_id == project_id))
            chapters = result.scalars().all()
            count = len(chapters)
            if count == 0:
                yield {"type": "error", "stage": "novel", "message": "未找到小说章节，请先上传"}
                return
            yield {"type": "progress", "stage": "novel", "progress": 100, "message": f"已解析 {count} 章节"}
            await update_pipeline_state(project_id, db, novel=StageStatus.DONE)
            await db.commit()
            yield {"type": "state_change", "stage": "novel", "status": StageStatus.DONE}
            yield {"type": "done", "stage": "novel"}

        agent_gen = novel_check()

    else:
        raise HTTPException(status_code=400, detail=f"未知阶段：{stage}")

    return StreamingResponse(
        sse_generator(project_id, stage, agent_gen, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{project_id}/cancel/{stage}")
async def cancel_stage(
    project_id: int,
    stage: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(project_id, user, db)
    pipeline = PipelineState(**project.pipeline_state)
    current_status = getattr(pipeline, stage, None)

    if current_status != StageStatus.RUNNING:
        raise HTTPException(status_code=400, detail="只有运行中的阶段可以取消")

    await update_pipeline_state(
        project_id,
        db,
        **{stage: StageStatus.CANCELLED},
        current_stage=None,
        current_progress=0,
        current_message=f"{stage} 已取消",
    )
    await db.commit()
    return {"code": 0, "msg": f"{stage} 已取消"}


@router.post("/{project_id}/chat/{stage}")
async def chat_stage(
    project_id: int,
    stage: str,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    节点内部 Chat 优化（返回 SSE 流）
    只有 outline / script / storyboard 支持
    """
    if stage not in CHAT_ENABLED_STAGES:
        raise HTTPException(status_code=400, detail=f"「{stage}」阶段不支持 Chat 优化")

    await get_project_or_404(project_id, user, db)
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")

    if stage == "outline":
        from app.agents.outline_agent import run_outline_chat
        agent_gen = run_outline_chat(project_id, message, db, user.id)
    elif stage == "script":
        from app.agents.script_agent import run_script_chat
        episode_index = body.get("episode_index")
        agent_gen = run_script_chat(project_id, message, db, user.id, episode_index)
    else:
        from app.agents.storyboard_agent import run_storyboard_chat
        agent_gen = run_storyboard_chat(project_id, message, db, user.id)

    async def stream():
        async for event in agent_gen:
            yield make_sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{project_id}/confirm/{stage}")
async def confirm_stage(
    project_id: int,
    stage: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    用户确认通过某个阶段（PAUSED → DONE）
    解锁下一步按钮
    """
    project = await get_project_or_404(project_id, user, db)
    pipeline = PipelineState(**project.pipeline_state)
    current_status = getattr(pipeline, stage, None)

    if current_status not in (StageStatus.PAUSED, StageStatus.RUNNING):
        raise HTTPException(status_code=400, detail=f"当前状态 {current_status} 不能确认")

    await update_pipeline_state(
        project_id, db,
        **{stage: StageStatus.DONE},
        current_stage=None,
        current_message="",
        current_progress=100,
    )
    await db.commit()
    return {"code": 0, "msg": f"{stage} 已确认完成"}


@router.post("/{project_id}/reset/{stage}")
async def reset_stage(
    project_id: int,
    stage: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    重置某个阶段为 PENDING（允许重新生成）
    同时将后续依赖阶段也重置
    """
    await get_project_or_404(project_id, user, db)

    # 找出需要连带重置的阶段
    reset_from = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1
    stages_to_reset = STAGE_ORDER[reset_from:] if reset_from >= 0 else [stage]

    await clear_stage_data(project_id, stage, db)

    updates = {s: StageStatus.PENDING for s in stages_to_reset}
    updates.update({"current_stage": None, "current_progress": 0, "current_message": "", "error": None})

    await update_pipeline_state(project_id, db, **updates)
    await db.commit()
    return {"code": 0, "msg": f"{stage} 及后续阶段已重置"}


@router.post("/{project_id}/clear/{stage}")
async def clear_stage(
    project_id: int,
    stage: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(project_id, user, db)

    clear_from = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1
    stages_to_reset = STAGE_ORDER[clear_from:] if clear_from >= 0 else [stage]

    await clear_stage_data(project_id, stage, db)

    updates = {s: StageStatus.PENDING for s in stages_to_reset}
    updates.update({"current_stage": None, "current_progress": 0, "current_message": "", "error": None})
    await update_pipeline_state(project_id, db, **updates)
    await db.commit()
    return {"code": 0, "msg": f"{stage} 及后续数据已清空"}

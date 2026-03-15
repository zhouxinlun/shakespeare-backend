from pydantic import BaseModel
from enum import Enum
from typing import Optional, Literal


class StageStatus(str, Enum):
    PENDING = "pending"    # 未开始，按钮可点击
    RUNNING = "running"    # 正在生成，SSE 推送进度
    PAUSED = "paused"      # 等待人工确认（Human-in-the-loop）
    DONE = "done"          # 完成
    FAILED = "failed"      # 失败，可重试
    CANCELLED = "cancelled"  # 用户主动取消

    # 用于跳过非必须步骤
    SKIPPED = "skipped"


PipelineStage = Literal["novel", "outline", "script", "storyboard", "images", "video"]


class PipelineState(BaseModel):
    novel: StageStatus = StageStatus.PENDING
    outline: StageStatus = StageStatus.PENDING
    script: StageStatus = StageStatus.PENDING
    storyboard: StageStatus = StageStatus.PENDING
    images: StageStatus = StageStatus.PENDING
    video: StageStatus = StageStatus.PENDING

    current_stage: Optional[str] = None
    current_progress: int = 0
    current_message: str = ""
    error: Optional[str] = None


# SSE 事件格式（后端推送给前端）
class SSEEvent(BaseModel):
    type: Literal["progress", "state_change", "content", "error", "pause", "done", "fallback_warning"]
    stage: str
    progress: Optional[int] = None
    message: Optional[str] = None
    status: Optional[StageStatus] = None
    data: Optional[dict] = None


# 阶段前置依赖：只有上一阶段 DONE 才能启动
STAGE_DEPS: dict[str, Optional[str]] = {
    "novel": None,
    "outline": "novel",
    "script": "outline",
    "storyboard": "script",
    "images": "storyboard",
    "video": "images",
}

# 支持 Chat 优化的阶段
CHAT_ENABLED_STAGES = {"outline", "script", "storyboard"}

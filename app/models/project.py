from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from typing import Optional
from app.database import Base
from app.core.time import utc_now_naive


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    intro: Mapped[Optional[str]] = mapped_column(String(1000))
    type: Mapped[Optional[str]] = mapped_column(String(100))
    content_type: Mapped[str] = mapped_column(String(50), default="short_drama")
    art_style: Mapped[Optional[str]] = mapped_column(String(200))
    video_ratio: Mapped[Optional[str]] = mapped_column(String(10), default="9:16")
    # Pipeline 状态机：存储每个阶段的状态
    pipeline_state: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {
            "novel": "pending",
            "outline": "pending",
            "script": "pending",
            "storyboard": "pending",
            "images": "pending",
            "video": "pending",
            "current_stage": None,
            "current_progress": 0,
            "current_message": "",
            "error": None,
        },
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive
    )

    __table_args__ = (
        Index("idx_projects_user_id_content_type", "user_id", "content_type"),
    )

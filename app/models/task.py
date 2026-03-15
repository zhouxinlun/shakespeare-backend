from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from typing import Optional
from app.database import Base
from app.core.time import utc_now_naive


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    task_type: Mapped[str] = mapped_column(String(50))  # image_gen | video_gen
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | done | failed
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(String(1000))
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive
    )

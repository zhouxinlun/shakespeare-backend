from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, DateTime, Text
from datetime import datetime
from typing import Optional
from app.database import Base
from app.core.time import utc_now_naive


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    intro: Mapped[Optional[str]] = mapped_column(Text)       # 人设/环境/功能描述
    prompt: Mapped[Optional[str]] = mapped_column(Text)      # 图生图 prompt
    type: Mapped[str] = mapped_column(String(20))            # role | props | scene
    episode_index: Mapped[Optional[int]] = mapped_column(Integer)
    file_path: Mapped[Optional[str]] = mapped_column(String(500))  # 已生成图片路径
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    outline_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("outlines.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive
    )

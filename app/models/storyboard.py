from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from app.database import Base
from app.core.time import utc_now_naive


class Storyboard(Base):
    __tablename__ = "storyboards"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_index: Mapped[int] = mapped_column(Integer)
    script_id: Mapped[int] = mapped_column(Integer, ForeignKey("scripts.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    # Array of Shot objects: [{id, title, cells:[{id,prompt,image_url}], asset_tags:[...]}]
    shots: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive
    )

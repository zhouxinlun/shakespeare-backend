from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Novel(Base):
    __tablename__ = "novels"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_index", name="uq_novels_project_chapter_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_index: Mapped[int] = mapped_column(Integer)
    volume: Mapped[Optional[str]] = mapped_column(String(200))
    chapter_title: Mapped[Optional[str]] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class NovelEvaluation(Base):
    __tablename__ = "novel_evaluations"
    __table_args__ = (
        Index("idx_novel_evaluations_novel_created", "novel_id", "created_at"),
        Index("idx_novel_evaluations_project_content_type", "project_id", "content_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(Integer, ForeignKey("novels.id", ondelete="CASCADE"))
    content_type: Mapped[str] = mapped_column(String(50), default="short_drama")
    evaluation_type: Mapped[str] = mapped_column(String(50), default="chapter_only")
    overall_score: Mapped[float] = mapped_column()
    dimension_scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    novel_revision: Mapped[int] = mapped_column(Integer, default=1)
    parent_evaluation_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("novel_evaluations.id", ondelete="SET NULL"),
    )
    model_used: Mapped[str] = mapped_column(String(100), default="novel_evaluator")
    prompt_version: Mapped[str] = mapped_column(String(50), default="short_drama.v1")
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

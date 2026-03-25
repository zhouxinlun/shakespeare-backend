from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )


class BookEvaluation(Base):
    __tablename__ = "book_evaluations"
    __table_args__ = (
        Index("idx_book_evaluations_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    content_type: Mapped[str] = mapped_column(String(50), default="short_drama")
    evaluated_novel_ids: Mapped[list] = mapped_column(JSONB, default=list)
    aggregated_stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    consistency_issues: Mapped[list] = mapped_column(JSONB, default=list)
    overall_assessment: Mapped[dict] = mapped_column(JSONB, default=dict)
    model_used: Mapped[str] = mapped_column(String(100), default="book_evaluator")
    prompt_version: Mapped[str] = mapped_column(String(50), default="book.v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )


class NovelChatSession(Base):
    __tablename__ = "novel_chat_sessions"
    __table_args__ = (
        Index(
            "idx_novel_chat_sessions_project_user_last_message",
            "project_id",
            "user_id",
            "last_message_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class NovelChatMessage(Base):
    __tablename__ = "novel_chat_messages"
    __table_args__ = (
        Index("idx_novel_chat_messages_project_user_created", "project_id", "user_id", "created_at"),
        Index("idx_novel_chat_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novel_chat_sessions.id", ondelete="CASCADE"),
    )
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    skill: Mapped[Optional[str]] = mapped_column(String(50))
    artifact_type: Mapped[Optional[str]] = mapped_column(String(50))
    artifact_status: Mapped[Optional[str]] = mapped_column(String(30))
    requires_confirmation: Mapped[bool] = mapped_column(default=False)
    artifact_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    selected_novel_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

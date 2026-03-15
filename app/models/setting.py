from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, DateTime, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from typing import Optional
from app.database import Base
from app.core.time import utc_now_naive


class AIConfig(Base):
    """AI 服务商配置（LLM / 图片 / 视频）"""
    __tablename__ = "ai_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20))       # text | image | video
    manufacturer: Mapped[str] = mapped_column(String(50))  # openai | anthropic | deepseek | volcengine | qwen | neuxnet | zhipu | gemini | xai
    model: Mapped[str] = mapped_column(String(200))
    api_key: Mapped[str] = mapped_column(String(500))
    base_url: Mapped[Optional[str]] = mapped_column(String(500))
    last_test_status: Mapped[Optional[str]] = mapped_column(String(20))
    last_test_summary: Mapped[Optional[str]] = mapped_column(String(2000))
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    supports_tools: Mapped[Optional[bool]] = mapped_column(Boolean)
    supports_thinking: Mapped[Optional[bool]] = mapped_column(Boolean)
    supports_vision: Mapped[Optional[bool]] = mapped_column(Boolean)
    supports_image_generation: Mapped[Optional[bool]] = mapped_column(Boolean)
    image_min_size: Mapped[Optional[str]] = mapped_column(String(20))
    supports_video_generation: Mapped[Optional[bool]] = mapped_column(Boolean)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class AIModelMap(Base):
    """Agent 功能 key → AI 配置映射"""
    __tablename__ = "ai_model_maps"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    config_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ai_configs.id", ondelete="SET NULL"))
    fallback_config_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)


class ProviderBaseURLMap(Base):
    """用户自定义 Provider 与 Base URL 前缀映射（1 Provider -> N URL）"""
    __tablename__ = "provider_base_url_maps"

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(50))
    base_url_prefix: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class Prompt(Base):
    """Agent 系统提示词（支持自定义覆盖）"""
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))           # mainAgent | subAgent | system
    parent_code: Mapped[Optional[str]] = mapped_column(String(100))
    default_value: Mapped[str] = mapped_column(String)
    custom_value: Mapped[Optional[str]] = mapped_column(String)

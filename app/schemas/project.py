from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Literal, Optional
from app.schemas.pipeline import PipelineState

PROJECT_TYPE_MAX_LEN = 100
VIDEO_RATIO_ALLOWED = {"9:16", "16:9"}
NAME_MAX_LEN = 200
INTRO_MAX_LEN = 1000
ART_STYLE_MAX_LEN = 200
CONTENT_TYPE_ALLOWED = {"short_drama", "web_novel", "mystery", "general"}


def _normalize_optional_text(value: Optional[str], *, field_name: str, max_len: int) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_len:
        raise ValueError(f"{field_name} 长度不能超过 {max_len} 个字符")
    return normalized


class ProjectCreate(BaseModel):
    name: str
    intro: Optional[str] = None
    type: Optional[str] = None
    content_type: Literal["short_drama", "web_novel", "mystery", "general"] = "short_drama"
    art_style: Optional[str] = None
    video_ratio: Optional[str] = "9:16"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name 不能为空")
        if len(normalized) > NAME_MAX_LEN:
            raise ValueError(f"name 长度不能超过 {NAME_MAX_LEN} 个字符")
        return normalized

    @field_validator("intro")
    @classmethod
    def validate_intro(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="intro", max_len=INTRO_MAX_LEN)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="type", max_len=PROJECT_TYPE_MAX_LEN)

    @field_validator("art_style")
    @classmethod
    def validate_art_style(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="art_style", max_len=ART_STYLE_MAX_LEN)

    @field_validator("video_ratio")
    @classmethod
    def validate_video_ratio(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return "9:16"
        normalized = value.strip()
        if not normalized:
            return "9:16"
        if normalized not in VIDEO_RATIO_ALLOWED:
            raise ValueError("video_ratio 仅支持 9:16 或 16:9")
        return normalized


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    intro: Optional[str] = None
    type: Optional[str] = None
    art_style: Optional[str] = None
    video_ratio: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name 不能为空")
        if len(normalized) > NAME_MAX_LEN:
            raise ValueError(f"name 长度不能超过 {NAME_MAX_LEN} 个字符")
        return normalized

    @field_validator("intro")
    @classmethod
    def validate_intro(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="intro", max_len=INTRO_MAX_LEN)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="type", max_len=PROJECT_TYPE_MAX_LEN)

    @field_validator("art_style")
    @classmethod
    def validate_art_style(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value, field_name="art_style", max_len=ART_STYLE_MAX_LEN)

    @field_validator("video_ratio")
    @classmethod
    def validate_video_ratio(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized not in VIDEO_RATIO_ALLOWED:
            raise ValueError("video_ratio 仅支持 9:16 或 16:9")
        return normalized


class ProjectOut(BaseModel):
    id: int
    name: str
    intro: Optional[str]
    type: Optional[str]
    content_type: str
    art_style: Optional[str]
    video_ratio: Optional[str]
    pipeline_state: PipelineState
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

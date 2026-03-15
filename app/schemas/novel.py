from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class NovelCreate(BaseModel):
    chapter_index: int
    volume: Optional[str] = None
    chapter_title: Optional[str] = None
    content: str

    @field_validator("chapter_index")
    @classmethod
    def validate_chapter_index(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("chapter_index 必须大于 0")
        return value

    @field_validator("volume", "chapter_title")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content 不能为空")
        return normalized


class NovelUpdate(BaseModel):
    chapter_index: Optional[int] = None
    volume: Optional[str] = None
    chapter_title: Optional[str] = None
    content: Optional[str] = None

    @field_validator("chapter_index")
    @classmethod
    def validate_chapter_index(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("chapter_index 必须大于 0")
        return value

    @field_validator("volume", "chapter_title")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("content 不能为空")
        return normalized


class NovelOut(BaseModel):
    id: int
    chapter_index: int
    volume: Optional[str]
    chapter_title: Optional[str]
    content: str
    word_count: int
    project_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NovelBatchCreate(BaseModel):
    chapters: list[NovelCreate]


class NovelReorderItem(BaseModel):
    novel_id: int
    chapter_index: int

    @field_validator("novel_id", "chapter_index")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("字段必须为正整数")
        return value


class NovelReorderRequest(BaseModel):
    orders: list[NovelReorderItem]


class NovelStatsOut(BaseModel):
    total_chapters: int
    total_words: int
    total_volumes: int
    average_score: Optional[float] = None


class NovelParseRequest(BaseModel):
    raw_text: str
    mode: Literal["auto", "rule_only", "ai_only"] = "auto"
    rule_type: Optional[Literal["title", "separator", "rhythm"]] = None
    separator_pattern: Optional[str] = None
    twist_strategy: Optional[Literal["aggressive", "balanced", "conservative"]] = None
    cliffhanger_style: Optional[Literal["suspense", "reversal", "climax", "dialogue"]] = None
    target_platform: Optional[str] = None
    target_audience: Optional[str] = None
    content_genre: Optional[str] = None

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("raw_text 不能为空")
        return normalized

    @field_validator("separator_pattern", "target_platform", "target_audience", "content_genre")
    @classmethod
    def normalize_optional_parse_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class NovelEvaluateLiveRequest(BaseModel):
    temporary_content: str
    chapter_title: Optional[str] = None

    @field_validator("temporary_content")
    @classmethod
    def validate_temporary_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("temporary_content 不能为空")
        return normalized

    @field_validator("chapter_title")
    @classmethod
    def normalize_chapter_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class NovelEvaluateBatchRequest(BaseModel):
    novel_ids: list[int]

    @field_validator("novel_ids")
    @classmethod
    def validate_novel_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("novel_ids 不能为空")
        if len(value) != len(set(value)):
            raise ValueError("novel_ids 不能重复")
        if any(item <= 0 for item in value):
            raise ValueError("novel_ids 必须全部为正整数")
        return value


class NovelEvaluationSuggestion(BaseModel):
    dimension: str
    issue: str
    suggestion: str
    priority: Literal["high", "medium", "low"] = "medium"
    text_ref: Optional[str] = None


class NovelEvaluationOut(BaseModel):
    id: int
    novel_id: int
    content_type: str
    evaluation_type: str
    overall_score: float
    dimension_scores: dict
    summary: Optional[str]
    suggestions: list[dict]
    novel_revision: int
    parent_evaluation_id: Optional[int] = None
    model_used: str
    prompt_version: str
    project_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class NovelLatestEvaluationOut(BaseModel):
    novel_id: int
    evaluation: NovelEvaluationOut


class ParsedChapterPayload(BaseModel):
    volume: Optional[str] = None
    chapter_index: int = Field(default=1, ge=1)
    chapter_title: Optional[str] = None
    content: str

    @field_validator("volume", "chapter_title")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content 不能为空")
        return normalized

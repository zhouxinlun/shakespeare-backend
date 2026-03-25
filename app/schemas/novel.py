from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
    parse_path: Literal["guided_rule", "intelligent"] = "guided_rule"
    rule_type: Optional[Literal["title", "separator", "custom"]] = None
    separator_pattern: Optional[str] = None
    custom_split_rule: Optional[str] = None
    twist_strategy: Optional[Literal["aggressive", "balanced", "conservative"]] = None
    cliffhanger_style: Optional[Literal["suspense", "reversal", "climax", "dialogue"]] = None
    content_genre: Optional[str] = None

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("raw_text 不能为空")
        return normalized

    @field_validator("separator_pattern", "custom_split_rule", "content_genre")
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


ChatSkillLiteral = Literal[
    "chapter_eval",
    "chapter_rewrite",
    "story_overview",
    "character_insight",
    "platform_advice",
]


class NovelChatRequest(BaseModel):
    message: str
    skill: Optional[ChatSkillLiteral] = None
    novel_ids: Optional[list[int]] = None
    session_id: Optional[int] = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message 不能为空")
        return normalized

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("session_id 必须为正整数")
        return value

    @field_validator("novel_ids")
    @classmethod
    def validate_novel_ids(cls, value: Optional[list[int]]) -> Optional[list[int]]:
        if value is None:
            return None
        if not value:
            return None
        if len(value) != len(set(value)):
            raise ValueError("novel_ids 不能重复")
        if any(item <= 0 for item in value):
            raise ValueError("novel_ids 必须全部为正整数")
        return value


class NovelChatMessageOut(BaseModel):
    id: int
    session_id: int
    role: Literal["user", "assistant"]
    message: str
    skill: Optional[ChatSkillLiteral] = None
    artifact_type: Optional[str] = None
    artifact_status: Optional[str] = None
    requires_confirmation: bool = False
    artifact_payload: Optional[dict] = None
    novel_ids: list[int] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class NovelChatHistoryOut(BaseModel):
    total: int
    messages: list[NovelChatMessageOut]


class NovelChatSessionOut(BaseModel):
    id: int
    title: Optional[str] = None
    preview: Optional[str] = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime

    model_config = {"from_attributes": True}


class NovelChatSessionListOut(BaseModel):
    total: int
    sessions: list[NovelChatSessionOut]


class NovelRewriteApplyRequest(BaseModel):
    instruction: Optional[str] = None
    scope_label: Optional[str] = None
    reason: Optional[str] = None
    chapter_index: Optional[int] = None
    chapter_title: Optional[str] = None
    original_snippet: Optional[str] = None
    replacement_snippet: Optional[str] = None
    full_content: Optional[str] = None

    @field_validator(
        "instruction",
        "scope_label",
        "reason",
        "chapter_title",
        "original_snippet",
        "replacement_snippet",
        "full_content",
    )
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("chapter_index")
    @classmethod
    def validate_chapter_index(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("chapter_index 必须为正整数")
        return value

    @model_validator(mode="after")
    def validate_has_rewrite_context(self):
        if not any(
            [
                self.instruction,
                self.reason,
                self.replacement_snippet,
                self.full_content,
            ]
        ):
            raise ValueError("至少提供一项改写说明")
        return self


class NovelRewriteApplyResult(BaseModel):
    chapter_title: Optional[str] = None
    content: str
    rationale: Optional[str] = None

    @field_validator("chapter_title", "rationale")
    @classmethod
    def normalize_optional_result_text(cls, value: Optional[str]) -> Optional[str]:
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


class NovelEvaluateBookRequest(BaseModel):
    novel_ids: Optional[list[int]] = None
    chapters_to_evaluate: Optional[list[int]] = None
    focus_areas: Optional[list[str]] = None
    include_benchmarking: bool = True
    force_re_evaluate: bool = False

    @field_validator("novel_ids", "chapters_to_evaluate")
    @classmethod
    def validate_optional_id_list(cls, value: Optional[list[int]]) -> Optional[list[int]]:
        if value is None:
            return None
        if not value:
            raise ValueError("列表不能为空")
        if len(value) != len(set(value)):
            raise ValueError("列表内不能重复")
        if any(item <= 0 for item in value):
            raise ValueError("列表元素必须全部为正整数")
        return value

    @field_validator("focus_areas")
    @classmethod
    def normalize_focus_areas(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None


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


class BookEvaluationOut(BaseModel):
    id: int
    project_id: int
    content_type: str
    evaluated_novel_ids: list[int]
    aggregated_stats: dict
    consistency_issues: list[dict]
    overall_assessment: dict
    model_used: str
    prompt_version: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class BookEvaluationHistoryOut(BaseModel):
    total: int
    evaluations: list[BookEvaluationOut]


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

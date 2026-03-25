from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.services.llm import call_llm_structured


class StorylineStage(BaseModel):
    title: str
    summary: str
    chapters: list[int] = Field(default_factory=list)
    tension: Optional[str] = None


class StorylineArtifact(BaseModel):
    title: str = "故事线时序图"
    stages: list[StorylineStage] = Field(default_factory=list)


class CharacterNode(BaseModel):
    id: str
    name: str
    role: str
    note: Optional[str] = None


class CharacterEdge(BaseModel):
    source: str
    target: str
    relation: str
    tension: Optional[str] = None


class CharacterTopologyArtifact(BaseModel):
    title: str = "人物关系拓扑图"
    center_label: str = "人物关系"
    nodes: list[CharacterNode] = Field(default_factory=list)
    edges: list[CharacterEdge] = Field(default_factory=list)


def _chapter_title(novel: Novel) -> str:
    return novel.chapter_title or f"第{novel.chapter_index}章"


def _selected_novel_context(selected_novels: list[Novel]) -> str:
    return "\n\n".join(
        [
            f"## 第{item.chapter_index}章《{_chapter_title(item)}》\n{(item.content or '').strip()[:4000]}"
            for item in selected_novels[:8]
        ]
    )


async def generate_storyline_artifact(
    *,
    selected_novels: list[Novel],
    db: AsyncSession,
    user_id: int,
    content_type: str,
) -> dict | None:
    if not selected_novels:
        return None

    result = await call_llm_structured(
        messages=[
            {
                "role": "user",
                "content": (
                    f"内容类型：{content_type}\n"
                    "请基于以下章节，提炼一个粗粒度的叙事时序图：\n\n"
                    f"{_selected_novel_context(selected_novels)}"
                ),
            }
        ],
        config_key="novel_evaluator",
        response_model=StorylineArtifact,
        db=db,
        user_id=user_id,
        system_prompt=(
            "你是故事策划编辑。请输出粗粒度故事线时序图。\n"
            "要求：\n"
            "1. stages 给出 4-8 个阶段。\n"
            "2. 每个阶段要包含 title、summary、chapters、tension。\n"
            "3. chapters 填章节号数组。\n"
            "4. title 要简洁，summary 用一句话概括叙事推进。"
        ),
    )
    return result.model_dump(mode="json")


async def generate_character_topology_artifact(
    *,
    selected_novels: list[Novel],
    db: AsyncSession,
    user_id: int,
    content_type: str,
) -> dict | None:
    if not selected_novels:
        return None

    result = await call_llm_structured(
        messages=[
            {
                "role": "user",
                "content": (
                    f"内容类型：{content_type}\n"
                    "请基于以下章节，梳理核心人物关系拓扑：\n\n"
                    f"{_selected_novel_context(selected_novels)}"
                ),
            }
        ],
        config_key="novel_evaluator",
        response_model=CharacterTopologyArtifact,
        db=db,
        user_id=user_id,
        system_prompt=(
            "你是人物关系分析师。请输出人物关系拓扑图。\n"
            "要求：\n"
            "1. nodes 至少 3 个，最多 8 个。\n"
            "2. edges 描述 source 与 target 的关系。\n"
            "3. relation 要简短，如“恋人/敌对/利用/师徒/亲属/盟友”。\n"
            "4. tension 描述当前冲突张力或潜在反转。"
        ),
    )
    return result.model_dump(mode="json")


def extract_tagged_section(content: str, labels: list[str]) -> str | None:
    for label in labels:
        escaped = re.escape(label)
        match = re.search(rf"【{escaped}】\s*([\s\S]*?)(?=\n【[^\n]+】|$)", content, re.I)
        value = match.group(1).strip() if match else ""
        if value:
            return value
    return None


def extract_indexed_tagged_section(content: str, index: int, labels: list[str]) -> str | None:
    for label in labels:
        escaped = re.escape(label)
        match = re.search(
            rf"【修改项\s*{index}\s*[-：:]\s*{escaped}】\s*([\s\S]*?)(?=\n【修改项\s*\d+\s*[-：:]\s*[^\n]+】|\n【[^\n]+】|$)",
            content,
            re.I,
        )
        value = match.group(1).strip() if match else ""
        if value:
            return value
    return None


def parse_chapter_index(content: str | None) -> int | None:
    if not content:
        return None
    matched = re.search(r"第\s*(\d+)\s*[章节回集]", content)
    if not matched:
        return None
    try:
        return int(matched.group(1))
    except (TypeError, ValueError):
        return None


def build_rewrite_artifact_from_text(content: str) -> dict | None:
    normalized = (content or "").strip()
    if not normalized:
        return None

    scope_label = extract_tagged_section(normalized, ["修改范围", "可替换章节", "目标章节"])
    reason = extract_tagged_section(normalized, ["改写意图", "修改意图", "改写目标"])
    changes: list[dict] = []

    for index in range(1, 9):
        chapter_section = extract_indexed_tagged_section(normalized, index, ["章节", "目标章节"])
        full_content = extract_indexed_tagged_section(
            normalized,
            index,
            ["整章替换正文", "可替换正文", "替换正文", "改写正文"],
        )
        if not chapter_section and not full_content:
            continue
        if not full_content:
            continue
        changes.append(
            {
                "chapter_index": parse_chapter_index(chapter_section),
                "chapter_title": extract_indexed_tagged_section(normalized, index, ["标题", "可替换标题"]),
                "reason": extract_indexed_tagged_section(normalized, index, ["修改原因", "改写原因", "说明"]) or reason,
                "original_snippet": extract_indexed_tagged_section(normalized, index, ["原文定位", "原文片段", "修改前片段"]),
                "replacement_snippet": extract_indexed_tagged_section(normalized, index, ["建议替换片段", "修改后片段", "替换后片段"]),
                "full_content": full_content,
            }
        )

    if not changes:
        body = extract_tagged_section(normalized, ["整章替换正文", "可替换正文", "替换正文", "改写正文"])
        if not body:
            loose_match = re.search(r"(?:完整)?第\s*(\d+)\s*章正文[：:\s-]*([\s\S]+)", normalized, re.I)
            if not loose_match:
                return None
            chapter_index = int(loose_match.group(1))
            body = loose_match.group(2).strip()
            changes.append(
                {
                    "chapter_index": chapter_index,
                    "chapter_title": None,
                    "reason": reason or "根据上一轮建议生成的待确认修改方案",
                    "original_snippet": None,
                    "replacement_snippet": None,
                    "full_content": body,
                }
            )
        else:
            chapter_section = extract_tagged_section(normalized, ["可替换章节", "目标章节"])
            changes.append(
                {
                    "chapter_index": parse_chapter_index(chapter_section or normalized),
                    "chapter_title": extract_tagged_section(normalized, ["可替换标题", "替换标题"]),
                    "reason": reason,
                    "original_snippet": None,
                    "replacement_snippet": None,
                    "full_content": body,
                }
            )

    return {
        "scope_label": scope_label,
        "reason": reason,
        "changes": changes,
    }

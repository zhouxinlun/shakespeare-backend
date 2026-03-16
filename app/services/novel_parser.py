import json
import re
from typing import AsyncIterator, Optional

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm import call_llm_structured


CHAPTER_PATTERNS = [
    r"^第[一二三四五六七八九十百千\d]+[章节回节]",
    r"^chapter\s*\d+",
    r"^\d+[\.、]\s*\S+",
    r"^[【\[]第?[一二三四五六七八九十百千\d]+",
    r"^【第[一二三四五六七八九十百千\d]+[章节回节]】",
    r"^「第[一二三四五六七八九十百千\d]+[章节回节]」",
    r"^【[\d一二三四五六七八九十百千]+】",
    r"^\*{2,}.*第[一二三四五六七八九十百千\d]+[章节回节]",
    r"^#{1,3}\s*第[一二三四五六七八九十百千\d]+[章节回节]",
]

VOLUME_PATTERNS = [
    r"^第[一二三四五六七八九十百千\d]+[卷部篇册]",
    r"^[上中下]部",
    r"^part\s*\d+",
    r"^volume\s*\d+",
]

TWIST_MARKERS = (
    "突然",
    "却",
    "竟然",
    "没想到",
    "原来",
    "但是",
    "然而",
    "就在这时",
    "下一秒",
    "结果",
    "真相",
)

COMMON_SEPARATOR_PATTERNS = (
    "---",
    "———",
    "***",
    "===",
    "~~~",
)


class _AINovelChapter(BaseModel):
    volume: Optional[str] = None
    chapter_index: Optional[int] = Field(default=None, ge=1)
    chapter_title: Optional[str] = None
    content: str


class _AINovelResult(BaseModel):
    chapters: list[_AINovelChapter]


class NovelParser:
    """小说章节解析：规则预处理 + AI 结构化解析。"""

    MAX_AI_CHARS = 30000
    PARAGRAPH_FALLBACK_MAX_CHARS = 1500

    PARSER_SYSTEM_PROMPT = """你是专业小说结构分析师。请将输入文本拆分为章节数组。

要求：
1. 识别卷结构（第一卷/Part/Volume 等）。
2. 识别章节结构（第一章/Chapter 1/1. 标题 等）。
3. 章节必须包含 content，且 content 不能丢字、漏段。
4. 若没有卷信息，volume 统一填“正文”。
5. 若没有章节标题，自动补“第N章”。

输出 JSON 对象，格式：
{
  "chapters": [
    {
      "volume": "第一卷",
      "chapter_index": 1,
      "chapter_title": "第一章",
      "content": "..."
    }
  ]
}
"""

    REFINER_SYSTEM_PROMPT = """你是小说结构修订助手。请基于输入的粗拆分章节：
1. 修正卷名、章节名、章节边界。
2. 不要丢失任何正文内容。
3. 保持章节顺序，chapter_index 从 1 开始连续。

输出 JSON 对象：{"chapters": [...]}。"""

    def __init__(self) -> None:
        self._chapter_res = [re.compile(p, re.IGNORECASE) for p in CHAPTER_PATTERNS]
        self._volume_res = [re.compile(p, re.IGNORECASE) for p in VOLUME_PATTERNS]

    async def parse(
        self,
        *,
        raw_text: str,
        mode: str,
        db: AsyncSession,
        user_id: int,
        options: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            yield {"type": "error", "message": "文本不能为空"}
            return

        options = options or {}
        parse_path = options.get("parse_path")
        legacy_rule_type = options.get("rule_type")
        if parse_path not in {"guided_rule", "intelligent"}:
            parse_path = "intelligent" if legacy_rule_type == "rhythm" else "guided_rule"

        rule_type = legacy_rule_type
        if parse_path == "guided_rule":
            if rule_type not in {"title", "separator", "custom"}:
                rule_type = "title"
        else:
            # 智能分集以 AI 节奏拆分为主，不再暴露 rhythm 规则型分支
            rule_type = "title"

        analysis = self._analyze_text(text=text, separator_pattern=options.get("separator_pattern"))

        yield {"type": "progress", "message": "正在分析文本结构...", "progress": 10}
        yield {"type": "analysis", "data": analysis}

        chapters: list[dict] = []
        fallback_events: list[dict] = []
        parsing_method = "rule_only"
        confidence = 0.7
        parser_prompt = self._build_parser_system_prompt(
            options=options,
            analysis=analysis,
            parse_path=parse_path,
            rule_type=rule_type,
        )

        if parse_path == "guided_rule":
            if rule_type == "separator":
                yield {"type": "progress", "message": "正在按分隔标记拆分文本...", "progress": 22}
                chapters = self._separator_parse(text, separator_pattern=options.get("separator_pattern"))
                if chapters:
                    parsing_method = "separator_rule"
                    confidence = 0.9
                    yield {"type": "progress", "message": "分隔标记解析完成", "progress": 60}
                elif mode == "rule_only":
                    yield {"type": "progress", "message": "未识别到有效分隔标记，已回退为规则解析", "progress": 28}
            elif rule_type == "custom":
                yield {"type": "progress", "message": "正在按自定义规则拆分文本...", "progress": 22}
                chapters = self._custom_rule_parse(text, custom_rule=options.get("custom_split_rule"))
                if chapters:
                    parsing_method = "custom_rule"
                    confidence = 0.9
                    yield {"type": "progress", "message": "自定义规则解析完成", "progress": 60}
                elif mode == "rule_only":
                    yield {"type": "progress", "message": "自定义规则未命中，已回退为规则解析", "progress": 28}

            if mode == "rule_only" and not chapters:
                chapters = self._rule_parse(text, allow_paragraph_fallback=True)
                yield {"type": "progress", "message": "规则解析完成", "progress": 60}
            elif mode == "ai_only" and not chapters:
                async for event in self._ai_parse(
                    text=text,
                    db=db,
                    user_id=user_id,
                    progress_from=20,
                    progress_to=58,
                    system_prompt=parser_prompt,
                ):
                    if event["type"] == "_ai_parse_result":
                        chapters = event["chapters"]
                    else:
                        yield event
                parsing_method = "ai_full"
                confidence = 0.9
                yield {"type": "progress", "message": "AI 解析完成", "progress": 60}
                if not chapters:
                    chapters = self._rule_parse(text, allow_paragraph_fallback=True)
                    parsing_method = "rule_fallback"
                    confidence = 0.45
                    yield {"type": "progress", "message": "AI 未返回结果，已回退到规则解析", "progress": 65}
            elif not chapters:
                rough = self._rule_parse(text, allow_paragraph_fallback=False)
                quality = self._assess_quality(rough)
                yield {
                    "type": "progress",
                    "message": f"规则预处理识别到 {len(rough)} 个候选章节，质量评估为 {quality}...",
                    "progress": 35,
                }
                if quality == "good":
                    chapters = rough
                    parsing_method = "rule_only"
                    confidence = 0.95
                    yield {"type": "progress", "message": "章节标记清晰，采用规则解析结果", "progress": 65}
                else:
                    if quality == "partial":
                        chapters, fallback_events = await self._ai_refine(rough_chunks=rough, db=db, user_id=user_id)
                        parsing_method = "ai_enhance"
                        confidence = 0.72
                        if chapters:
                            yield {"type": "progress", "message": "AI 解析完成", "progress": 65}

                    if not chapters:
                        async for event in self._ai_parse(
                            text=text,
                            db=db,
                            user_id=user_id,
                            progress_from=42,
                            progress_to=58,
                            system_prompt=parser_prompt,
                        ):
                            if event["type"] == "_ai_parse_result":
                                chapters = event["chapters"]
                            else:
                                yield event
                        parsing_method = "ai_full"
                        confidence = 0.88
                        if chapters:
                            yield {"type": "progress", "message": "AI 解析完成", "progress": 65}

                    if not chapters:
                        chapters = rough or self._rule_parse(text, allow_paragraph_fallback=True)
                        parsing_method = "rule_fallback"
                        confidence = 0.4
                        yield {"type": "progress", "message": "AI 未返回可用结果，采用规则回退结果", "progress": 65}
        else:
            if mode == "rule_only":
                chapters = self._rhythm_rule_parse(
                    text,
                    analysis=analysis,
                    twist_strategy=options.get("twist_strategy"),
                )
                parsing_method = "rhythm_rule"
                confidence = 0.72
                yield {"type": "progress", "message": "按剧情起伏规则拆分完成", "progress": 60}
            else:
                yield {"type": "progress", "message": "正在按转折点和挂念策略进行智能分集...", "progress": 24}
                async for event in self._ai_parse(
                    text=text,
                    db=db,
                    user_id=user_id,
                    progress_from=28,
                    progress_to=58,
                    system_prompt=parser_prompt,
                ):
                    if event["type"] == "_ai_parse_result":
                        chapters = event["chapters"]
                    else:
                        yield event
                parsing_method = "rhythm_ai"
                confidence = 0.86
                if chapters:
                    yield {"type": "progress", "message": "智能分集完成", "progress": 60}

            if self._need_rhythm_fallback(chapters=chapters, text=text, analysis=analysis):
                rhythm_chapters = self._rhythm_rule_parse(
                    text,
                    analysis=analysis,
                    twist_strategy=options.get("twist_strategy"),
                )
                if len(rhythm_chapters) >= 2:
                    chapters = rhythm_chapters
                    parsing_method = "rhythm_rule"
                    confidence = 0.78
                    yield {"type": "progress", "message": "检测到单段分集，已按剧情起伏自动细分", "progress": 64}

            if not chapters:
                chapters = self._rule_parse(text, allow_paragraph_fallback=True)
                parsing_method = "rule_fallback"
                confidence = 0.45
                yield {"type": "progress", "message": "智能分集未返回结果，已回退为规则解析", "progress": 65}

        for event in fallback_events:
            yield event

        chapters = self._normalize_chapters(chapters)
        if not chapters:
            yield {"type": "error", "message": "未识别到有效章节，请检查文本格式"}
            return

        total = len(chapters)
        volumes = {c.get("volume") or "正文" for c in chapters}
        for idx, chapter in enumerate(chapters, start=1):
            progress = min(95, 65 + int(idx / total * 30))
            yield {
                "type": "progress",
                "message": f"正在输出解析结果（{idx}/{total}）...",
                "progress": progress,
            }
            yield {"type": "chunk", "data": chapter}

        yield {
            "type": "done",
            "total_chapters": total,
            "total_volumes": len(volumes),
            "progress": 100,
            "parsing_method": parsing_method,
            "confidence": confidence,
        }

    def _rule_parse(self, text: str, *, allow_paragraph_fallback: bool = True) -> list[dict]:
        lines = text.split("\n")
        current_volume = "正文"
        current_title: Optional[str] = None
        current_content: list[str] = []
        chapters: list[dict] = []
        saw_structure_heading = False

        def flush_current() -> None:
            nonlocal current_title, current_content
            content = "\n".join(current_content).strip()
            if not content and not current_title:
                current_title = None
                current_content = []
                return
            chapter_index = len(chapters) + 1
            chapters.append(
                {
                    "volume": current_volume,
                    "chapter_index": chapter_index,
                    "chapter_title": current_title or f"第{chapter_index}章",
                    "content": content,
                }
            )
            current_title = None
            current_content = []

        for raw_line in lines:
            line = raw_line.strip()

            if self._is_volume_heading(line):
                saw_structure_heading = True
                if current_title or "".join(current_content).strip():
                    flush_current()
                current_volume = line
                continue

            if self._is_chapter_heading(line):
                saw_structure_heading = True
                if current_title or "".join(current_content).strip():
                    flush_current()
                current_title = line
                continue

            current_content.append(raw_line)

        flush_current()

        if chapters and saw_structure_heading:
            return chapters
        if not allow_paragraph_fallback:
            return []

        # 未命中章节标题时，按段落聚合成章节
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if not paragraphs:
            return []

        chunks: list[str] = []
        buffer = ""
        for p in paragraphs:
            candidate = f"{buffer}\n\n{p}" if buffer else p
            if not buffer or self._compact_len(candidate) <= self.PARAGRAPH_FALLBACK_MAX_CHARS:
                buffer = candidate
            else:
                chunks.append(buffer)
                buffer = p
        if buffer:
            chunks.append(buffer)

        return [
            {
                "volume": "正文",
                "chapter_index": i + 1,
                "chapter_title": f"第{i + 1}章",
                "content": c,
            }
            for i, c in enumerate(chunks)
        ]

    def _assess_quality(self, chapters: list[dict]) -> str:
        if not chapters:
            return "none"

        explicit_titles = sum(
            1
            for chapter in chapters
            if chapter.get("chapter_title")
            and not self._is_auto_generated_title(chapter["chapter_title"])
        )

        if len(chapters) >= 3 and explicit_titles >= 2:
            return "good"
        if len(chapters) >= 10:
            return "good"
        if len(chapters) >= 2:
            return "partial"
        return "none"

    def _is_auto_generated_title(self, title: str) -> bool:
        return bool(re.fullmatch(r"第(?:\d+|[一二三四五六七八九十百千万]+)章", (title or "").strip()))

    def _separator_parse(self, text: str, *, separator_pattern: Optional[str] = None) -> list[dict]:
        chunks: list[str] = []
        buffer: list[str] = []
        separator_value = (separator_pattern or "").strip()

        for raw_line in text.split("\n"):
            if self._is_separator_line(raw_line, separator_pattern=separator_value):
                content = "\n".join(buffer).strip()
                if content:
                    chunks.append(content)
                buffer = []
                continue
            buffer.append(raw_line)

        content = "\n".join(buffer).strip()
        if content:
            chunks.append(content)

        if len(chunks) < 2:
            return []

        return [
            {
                "volume": "正文",
                "chapter_index": index,
                "chapter_title": f"第{index}章",
                "content": chunk,
            }
            for index, chunk in enumerate(chunks, start=1)
        ]

    def _custom_rule_parse(self, text: str, *, custom_rule: Optional[str]) -> list[dict]:
        rule = (custom_rule or "").strip()
        if not rule:
            return []

        if rule.startswith("re:"):
            pattern = rule[3:].strip()
            if not pattern:
                return []
            try:
                regex = re.compile(pattern)
            except re.error:
                return []
            chunks = [part.strip() for part in re.split(regex, text) if part.strip()]
        else:
            chunks = []
            buffer: list[str] = []
            for raw_line in text.split("\n"):
                if raw_line.strip() == rule:
                    content = "\n".join(buffer).strip()
                    if content:
                        chunks.append(content)
                    buffer = []
                    continue
                buffer.append(raw_line)
            content = "\n".join(buffer).strip()
            if content:
                chunks.append(content)

        if len(chunks) < 2:
            return []

        return [
            {
                "volume": "正文",
                "chapter_index": index,
                "chapter_title": f"第{index}章",
                "content": chunk,
            }
            for index, chunk in enumerate(chunks, start=1)
        ]

    def _need_rhythm_fallback(self, *, chapters: list[dict], text: str, analysis: dict) -> bool:
        if len(chapters) >= 2:
            return False
        total_chars = self._compact_len(text)
        if total_chars < 500:
            return False
        paragraphs = int(analysis.get("paragraphs", 0) or 0)
        twist_markers = int(analysis.get("twist_marker_count", 0) or 0)
        # 短篇智能分集场景中，单段结果往往不符合短剧观看节奏，适当前置回退拆分。
        return total_chars >= 800 or paragraphs >= 3 or twist_markers >= 1

    def _rhythm_rule_parse(
        self,
        text: str,
        *,
        analysis: Optional[dict] = None,
        twist_strategy: Optional[str] = None,
    ) -> list[dict]:
        compact_text = self._compact_len(text)
        if compact_text < 400:
            return []

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(paragraphs) < 2:
            paragraphs = [p.strip() for p in self._split_sentences(text) if p.strip()]
        if len(paragraphs) < 2:
            return []

        twist_count = int((analysis or {}).get("twist_marker_count", 0) or 0)
        target_count = self._estimate_rhythm_target_count(
            total_chars=compact_text,
            paragraph_count=len(paragraphs),
            twist_count=twist_count,
            twist_strategy=twist_strategy,
        )
        if target_count < 2:
            return []

        target_chunk_size = max(260, int(compact_text / target_count))
        min_chunk_size = max(180, int(target_chunk_size * 0.6))
        max_chunk_size = int(target_chunk_size * 1.4)

        chunks: list[str] = []
        buffer: list[str] = []
        buffer_size = 0

        for idx, paragraph in enumerate(paragraphs):
            para_text = paragraph.strip()
            if not para_text:
                continue
            para_size = self._compact_len(para_text)
            buffer.append(para_text)
            buffer_size += para_size

            is_last = idx == len(paragraphs) - 1
            next_para = paragraphs[idx + 1] if not is_last else ""
            has_twist_signal = any(marker in para_text for marker in TWIST_MARKERS)
            next_has_twist_signal = any(marker in next_para for marker in TWIST_MARKERS)

            if is_last:
                continue
            if buffer_size < min_chunk_size:
                continue

            should_split = False
            if buffer_size >= target_chunk_size and has_twist_signal:
                should_split = True
            elif buffer_size >= max_chunk_size:
                should_split = True
            elif has_twist_signal and not next_has_twist_signal and buffer_size >= min_chunk_size:
                should_split = True

            if should_split:
                chunks.append("\n\n".join(buffer).strip())
                buffer = []
                buffer_size = 0

        if buffer:
            chunks.append("\n\n".join(buffer).strip())

        chunks = [chunk for chunk in chunks if chunk]
        if len(chunks) < 2:
            return []

        if len(chunks) > 12:
            chunks = chunks[:12]

        tail_size = self._compact_len(chunks[-1])
        if len(chunks) >= 2 and tail_size < 120:
            chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}".strip()
            chunks = chunks[:-1]

        return [
            {
                "volume": "正文",
                "chapter_index": index,
                "chapter_title": f"第{index}章",
                "content": chunk,
            }
            for index, chunk in enumerate(chunks, start=1)
        ]

    def _estimate_rhythm_target_count(
        self,
        *,
        total_chars: int,
        paragraph_count: int,
        twist_count: int,
        twist_strategy: Optional[str],
    ) -> int:
        if total_chars < 1200:
            base = 2
        elif total_chars < 2600:
            base = 3
        elif total_chars < 4200:
            base = 4
        else:
            base = 5

        if twist_count >= 8:
            base += 2
        elif twist_count >= 4:
            base += 1

        if twist_strategy == "aggressive":
            base += 1
        elif twist_strategy == "conservative":
            base -= 1

        base = max(2, min(base, max(2, paragraph_count)))
        return min(base, 12)

    def _split_sentences(self, text: str) -> list[str]:
        sentence_parts = re.split(r"(?<=[。！？!?；;])\s*", text)
        return [part.strip() for part in sentence_parts if part.strip()]

    async def _ai_parse(
        self,
        *,
        text: str,
        db: AsyncSession,
        user_id: int,
        progress_from: int,
        progress_to: int,
        system_prompt: str,
    ) -> AsyncIterator[dict]:
        fallback_events: list[dict] = []

        async def on_fallback(event: dict) -> None:
            fallback_events.append(event)

        segments = self._split_text(text, max_size=self.MAX_AI_CHARS)
        parsed: list[dict] = []
        total_segments = len(segments)

        for index, segment in enumerate(segments, start=1):
            if total_segments > 1:
                yield {
                    "type": "progress",
                    "message": f"AI 正在解析分段（{index}/{total_segments}）...",
                    "progress": self._segment_progress(
                        index=index,
                        total=total_segments,
                        progress_from=progress_from,
                        progress_to=progress_to,
                    ),
                }
            try:
                result = await call_llm_structured(
                    messages=[{"role": "user", "content": segment}],
                    config_key="novel_parser",
                    response_model=_AINovelResult,
                    db=db,
                    user_id=user_id,
                    system_prompt=system_prompt,
                    on_fallback=on_fallback,
                )
            except Exception:
                for event in fallback_events:
                    yield self._to_parse_fallback_event(event)
                yield {"type": "_ai_parse_result", "chapters": []}
                return

            parsed.extend([chapter.model_dump() for chapter in result.chapters])

        for event in fallback_events:
            yield self._to_parse_fallback_event(event)
        yield {"type": "_ai_parse_result", "chapters": parsed}

    async def _ai_refine(
        self,
        *,
        rough_chunks: list[dict],
        db: AsyncSession,
        user_id: int,
    ) -> tuple[list[dict], list[dict]]:
        payload = json.dumps(rough_chunks, ensure_ascii=False)
        if len(payload) > 120000:
            return rough_chunks, []

        fallback_events: list[dict] = []

        async def on_fallback(event: dict) -> None:
            fallback_events.append(event)

        try:
            result = await call_llm_structured(
                messages=[
                    {
                        "role": "user",
                        "content": f"请修订以下章节粗拆分结果：\n{payload}",
                    }
                ],
                config_key="novel_parser",
                response_model=_AINovelResult,
                db=db,
                user_id=user_id,
                system_prompt=self.REFINER_SYSTEM_PROMPT,
                on_fallback=on_fallback,
            )
        except Exception:
            return rough_chunks, [self._to_parse_fallback_event(e) for e in fallback_events]

        chapters = [chapter.model_dump() for chapter in result.chapters]
        if not chapters:
            return rough_chunks, [self._to_parse_fallback_event(e) for e in fallback_events]
        return chapters, [self._to_parse_fallback_event(e) for e in fallback_events]

    def _normalize_chapters(self, chapters: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for index, raw in enumerate(chapters, start=1):
            try:
                payload = _AINovelChapter.model_validate(raw)
            except ValidationError:
                continue
            content = (payload.content or "").strip()
            if not content:
                continue
            volume = (payload.volume or "正文").strip() or "正文"
            title = (payload.chapter_title or "").strip() or f"第{index}章"
            normalized.append(
                {
                    "volume": volume,
                    "chapter_index": index,
                    "chapter_title": title,
                    "content": content,
                }
            )
        return normalized

    def _is_chapter_heading(self, line: str) -> bool:
        if not line:
            return False
        return any(p.search(line) for p in self._chapter_res)

    def _is_volume_heading(self, line: str) -> bool:
        if not line:
            return False
        return any(p.search(line) for p in self._volume_res)

    def _is_separator_line(self, line: str, *, separator_pattern: Optional[str] = None) -> bool:
        stripped = (line or "").strip()
        if not stripped:
            return False
        if separator_pattern and stripped == separator_pattern:
            return True
        if stripped in COMMON_SEPARATOR_PATTERNS:
            return True
        return bool(re.fullmatch(r"[-=*~—]{3,}", stripped))

    def _split_text(self, text: str, *, max_size: int) -> list[str]:
        if len(text) <= max_size:
            return [text]

        pieces: list[str] = []
        remaining = text
        while len(remaining) > max_size:
            idx = remaining.rfind("\n", 0, max_size)
            if idx < int(max_size * 0.5):
                idx = max_size
            pieces.append(remaining[:idx].strip())
            remaining = remaining[idx:].strip()
        if remaining:
            pieces.append(remaining)
        return [p for p in pieces if p]

    def _compact_len(self, text: str) -> int:
        return len(re.sub(r"\s+", "", text or ""))

    def _analyze_text(self, *, text: str, separator_pattern: Optional[str] = None) -> dict:
        lines = [line.strip() for line in text.split("\n")]
        paragraphs = [p for p in re.split(r"\n\s*\n+", text) if p.strip()]
        chapter_heading_hits = sum(1 for line in lines if self._is_chapter_heading(line))
        separator_hits = sum(
            1 for line in lines if self._is_separator_line(line, separator_pattern=separator_pattern)
        )
        twist_marker_count = sum(text.count(marker) for marker in TWIST_MARKERS)
        suggested_path = "intelligent" if twist_marker_count >= 5 or chapter_heading_hits == 0 else "guided_rule"
        suggested_rule_type = "separator" if separator_hits >= 2 else "title"

        return {
            "total_chars": self._compact_len(text),
            "paragraphs": len(paragraphs),
            "chapter_heading_hits": chapter_heading_hits,
            "separator_hits": separator_hits,
            "twist_marker_count": twist_marker_count,
            "suggested_path": suggested_path,
            "suggested_rule_type": suggested_rule_type,
        }

    def _build_parser_system_prompt(self, *, options: dict, analysis: dict, parse_path: str, rule_type: str) -> str:
        prompt = self.PARSER_SYSTEM_PROMPT.strip()
        extras: list[str] = []

        if parse_path == "intelligent":
            extras.extend(
                [
                    "当前任务是为短剧创建分集方案，而不是识别已有章节标记。",
                    "分集目标：把故事拆成多个相对独立、各有吸引力的短集，避免整篇只产出一集。",
                    "观众视角：每集都应有推进价值，且结尾要保留继续观看动机。",
                    "分集数量参考：短篇（约500-1500字）优先拆为2-3集；中篇（约1500-4000字）优先拆为3-5集；长篇可拆为5-12集。",
                    "优先在以下边界分集：情绪转折、信息揭露、冲突升级、目标改变、关系变化、悬念建立。",
                    "每集理想结尾：反转、悬念、高潮、关键对话中断中的至少一种。",
                    "禁止机械按字数平均切分；也不要在毫无叙事意义的位置生硬断开。",
                ]
            )
        elif rule_type == "separator":
            extras.append("如果原文里存在明显分隔符，请优先将这些分隔符视为候选边界。")
        elif rule_type == "custom":
            extras.append("用户提供了自定义分割规则，请优先遵循该规则进行候选切分。")
        else:
            extras.append("如果原文已有明确章标题，请优先保留其结构和顺序。")

        twist_strategy = options.get("twist_strategy")
        if twist_strategy == "aggressive":
            extras.append("分段策略使用 aggressive：倾向于在每个明确转折点处分段，形成更密集的节奏。")
        elif twist_strategy == "conservative":
            extras.append("分段策略使用 conservative：只在主要转折点处分段，保持更强的叙事连贯性。")
        elif twist_strategy == "balanced":
            extras.append("分段策略使用 balanced：合并相关的小转折，兼顾节奏和连贯。")

        cliffhanger_style = options.get("cliffhanger_style")
        if cliffhanger_style:
            extras.append(f"章节结尾风格偏向 {cliffhanger_style}，请让标题和边界尽量服务于这种留念感。")

        if options.get("content_genre"):
            extras.append(f"内容类型为 {options['content_genre']}，请保持相应题材的节奏特征。")

        extras.append(
            f"本地启发式分析：检测到约 {analysis.get('chapter_heading_hits', 0)} 个标题标记、"
            f"{analysis.get('separator_hits', 0)} 个分隔标记、{analysis.get('twist_marker_count', 0)} 个转折提示词。"
        )

        return f"{prompt}\n\n补充策略：\n- " + "\n- ".join(extras)

    def _segment_progress(self, *, index: int, total: int, progress_from: int, progress_to: int) -> int:
        if total <= 0:
            return progress_from
        if progress_to <= progress_from:
            return progress_from
        step = int(index / total * (progress_to - progress_from))
        return min(progress_to, progress_from + step)

    def _to_parse_fallback_event(self, raw_event: dict) -> dict:
        return {
            "type": "fallback_warning",
            "message": raw_event.get("message", "模型已自动切换到备用模型"),
            "data": {
                "key": raw_event.get("key"),
                "from": raw_event.get("from_model"),
                "to": raw_event.get("to_model"),
                "reason": raw_event.get("reason"),
                "reset_content": bool(raw_event.get("reset_content")),
            },
        }

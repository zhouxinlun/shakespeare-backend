from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.services.llm import call_llm_structured


EVALUATION_PROFILES = {
    "short_drama": {
        "prompt_version": "short_drama.v2",
        "summary_focus": "按短剧留存和改编效率评估这一章是否适合作为独立单元或强节奏片段。",
        "dimensions": {
            "opening_hook": {
                "label": "开场 Hook",
                "weight": 0.20,
                "issue": "开场进入状态偏慢，前几秒的抓力不足。",
                "suggestion": "把最异常、最危险或最羞耻的瞬间前置，前两段内就交代核心冲突。",
            },
            "conflict_density": {
                "label": "冲突密度",
                "weight": 0.18,
                "issue": "中段有效冲突偏少，情绪或处境升级不够快。",
                "suggestion": "压缩铺垫，增加目标受阻、关系对撞或信息打脸的时刻，让局势持续升级。",
            },
            "twist_effectiveness": {
                "label": "转折力度",
                "weight": 0.18,
                "issue": "转折的意外性和方向改变感不够强。",
                "suggestion": "强化“观众原以为会这样，结果却那样”的落差，尽量让转折改变人物目标或情势。",
            },
            "cliffhanger_strength": {
                "label": "挂念强度",
                "weight": 0.20,
                "issue": "结尾停点偏平，缺少明确的追更驱动力。",
                "suggestion": "把结尾停在秘密将揭未揭、关系刚反转或危险刚爆发的瞬间，给观众一个必须看下一集的理由。",
            },
            "visual_adaptability": {
                "label": "画面可拍性",
                "weight": 0.12,
                "issue": "文本偏内心化或说明化，转成镜头语言的空间不足。",
                "suggestion": "多用动作、场景变化和可见的行为承载信息，少用大段解释性叙述。",
            },
            "serialized_drive": {
                "label": "追更驱动力",
                "weight": 0.12,
                "issue": "这一章作为连续剧单元的独立吸引力不足。",
                "suggestion": "明确这一章的核心问题和未完成事项，让人物在结尾带着更高代价进入下一段。",
            },
        },
    },
    "web_novel": {
        "prompt_version": "web_novel.v1",
        "summary_focus": "按网文连载标准评估章节的爽点、粘性和连载驱动。",
        "dimensions": {
            "plot_momentum": {
                "label": "情节推进",
                "weight": 0.22,
                "issue": "章节推进偏慢，主线获得感不足。",
                "suggestion": "让章节内至少完成一次推进、揭露或反打，避免纯铺垫段落占满整章。",
            },
            "character_appeal": {
                "label": "人物吸引力",
                "weight": 0.16,
                "issue": "主角或核心人物的辨识度不够强。",
                "suggestion": "加强人物欲望、态度和决策瞬间，让角色标签更鲜明。",
            },
            "readability": {
                "label": "可读性",
                "weight": 0.16,
                "issue": "句段说明感偏重，阅读节奏不够顺滑。",
                "suggestion": "拆短解释段，减少重复信息，让句子更直接、更利落。",
            },
            "immersion": {
                "label": "沉浸感",
                "weight": 0.14,
                "issue": "场景和情绪的代入感还不够稳定。",
                "suggestion": "补充关键感官信息和即时反应，让读者更容易进入现场。",
            },
            "chapter_payoff": {
                "label": "章节爽点",
                "weight": 0.16,
                "issue": "单章回报感不足，读完后记忆点不强。",
                "suggestion": "确保章节里有明确的赢点、爆点、揭露点或情绪兑现。",
            },
            "retention_drive": {
                "label": "追读驱动",
                "weight": 0.16,
                "issue": "章末的追读诱因不够强。",
                "suggestion": "把章末停在新危机、新问题或新优势刚出现的瞬间，提升追更欲望。",
            },
        },
    },
    "mystery": {
        "prompt_version": "mystery.v1",
        "summary_focus": "按悬疑叙事标准评估线索布置、公平性、悬念控制和揭晓冲击。",
        "dimensions": {
            "suspense_setup": {
                "label": "悬念搭建",
                "weight": 0.18,
                "issue": "悬念问题抛出不够鲜明，读者想追问的核心问题还不够聚焦。",
                "suggestion": "更早提出核心疑问，并确保这一章不断加深该疑问的紧迫性。",
            },
            "clue_fairness": {
                "label": "线索公平性",
                "weight": 0.16,
                "issue": "线索铺设偏少或偏隐，读者难以参与推理。",
                "suggestion": "补充可回看的有效线索，让信息既不直白泄底，也不完全缺席。",
            },
            "logic_consistency": {
                "label": "逻辑闭环",
                "weight": 0.18,
                "issue": "人物行为或事件因果存在逻辑松动。",
                "suggestion": "补齐动机、时间和因果链，避免关键转折只靠作者强推。",
            },
            "reveal_impact": {
                "label": "揭晓冲击",
                "weight": 0.18,
                "issue": "揭晓或反转的冲击力不够，难形成记忆点。",
                "suggestion": "让揭晓改变读者对前文的理解，而不是只补充一条普通信息。",
            },
            "atmosphere_control": {
                "label": "氛围控制",
                "weight": 0.14,
                "issue": "压迫感或不安感维持得不够稳定。",
                "suggestion": "通过环境细节、异常行为和时间压力持续维持危险氛围。",
            },
            "payoff_strength": {
                "label": "回收力度",
                "weight": 0.16,
                "issue": "这一章的伏笔回收或推进力度偏弱。",
                "suggestion": "让至少一条旧线索在本章获得推进、反证或局部兑现。",
            },
        },
    },
    "general": {
        "prompt_version": "general.v1",
        "summary_focus": "按通用叙事标准评估章节的完整度、吸引力和表达质量。",
        "dimensions": {
            "plot": {
                "label": "情节推进",
                "weight": 0.22,
                "issue": "情节推进力度不够，章节内变化偏少。",
                "suggestion": "让章节里发生更明确的局势变化或目标推进，减少平铺信息。",
            },
            "character": {
                "label": "人物塑造",
                "weight": 0.18,
                "issue": "人物立体度和辨识度还不够强。",
                "suggestion": "通过选择、冲突和反应来显露人物，而不是只用说明句介绍。",
            },
            "dialogue": {
                "label": "对话质量",
                "weight": 0.15,
                "issue": "对话的信息感和人物感不够鲜明。",
                "suggestion": "让对话承担冲突、态度和信息，而不只是重复已有叙述。",
            },
            "description": {
                "label": "场景描写",
                "weight": 0.13,
                "issue": "场景和动作描写存在泛化问题。",
                "suggestion": "增加更具体的动作、感官和空间信息，让读者更容易形成画面。",
            },
            "pacing": {
                "label": "节奏把控",
                "weight": 0.14,
                "issue": "章节节奏分布不够均衡，存在拖慢或跳跃感。",
                "suggestion": "收紧重复说明，把重信息放在更靠前的位置，保持段落节奏变化。",
            },
            "originality": {
                "label": "记忆点",
                "weight": 0.18,
                "issue": "本章独特性和记忆点偏弱。",
                "suggestion": "强化最特别的设定、关系或事件，让本章结束后能留下明确印象。",
            },
        },
    },
}


class _Suggestion(BaseModel):
    dimension: str
    issue: str
    suggestion: str
    priority: str = "medium"
    text_ref: Optional[str] = None


class _EvaluationResult(BaseModel):
    overall_score: Optional[float] = Field(default=None, ge=1, le=10)
    dimension_scores: dict[str, float]
    summary: str
    suggestions: list[_Suggestion] = Field(default_factory=list)


class _LiveEvaluationResult(BaseModel):
    overall_score: Optional[float] = Field(default=None, ge=1, le=10)
    dimension_scores: dict[str, float]


class NovelEvaluator:
    MODEL = "novel_evaluator"
    DEFAULT_MISSING_SCORE = 6.0

    def __init__(self, content_type: str = "short_drama") -> None:
        profile = EVALUATION_PROFILES.get(content_type) or EVALUATION_PROFILES["general"]
        self.content_type = content_type if content_type in EVALUATION_PROFILES else "general"
        self.profile = profile
        self.dimensions = list(profile["dimensions"].keys())
        self.weights = {
            key: float(value["weight"])
            for key, value in profile["dimensions"].items()
        }
        self.prompt_version = str(profile["prompt_version"])
        self.system_prompt = self._build_system_prompt()

    async def evaluate_single(
        self,
        *,
        novel: Novel,
        db: AsyncSession,
        user_id: int,
    ) -> tuple[dict, list[dict]]:
        fallback_events: list[dict] = []

        async def on_fallback(event: dict) -> None:
            fallback_events.append(event)

        result = await call_llm_structured(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"内容类型：{self.content_type}\n"
                        f"章节：{novel.chapter_title or f'第{novel.chapter_index}章'}\n\n"
                        f"正文：\n{novel.content}"
                    ),
                }
            ],
            config_key=self.MODEL,
            response_model=_EvaluationResult,
            db=db,
            user_id=user_id,
            system_prompt=self.system_prompt,
            on_fallback=on_fallback,
        )

        scores = self._normalize_scores(result.dimension_scores)
        overall_score = result.overall_score
        if overall_score is None:
            overall_score = self._weighted_average(scores)

        normalized = {
            "overall_score": round(float(overall_score), 2),
            "dimension_scores": {k: round(float(v), 2) for k, v in scores.items()},
            "summary": (result.summary or "").strip(),
            "suggestions": self._normalize_suggestions(
                suggestions=[s.model_dump() for s in result.suggestions],
                scores=scores,
                novel=novel,
            ),
            "content_type": self.content_type,
            "model_used": self.MODEL,
            "prompt_version": self.prompt_version,
        }
        return normalized, [self._to_eval_fallback_event(e) for e in fallback_events]

    async def evaluate_live(
        self,
        *,
        temporary_content: str,
        chapter_title: Optional[str],
        db: AsyncSession,
        user_id: int,
    ) -> dict:
        result = await call_llm_structured(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"内容类型：{self.content_type}\n"
                        f"章节：{chapter_title or '未命名章节'}\n\n"
                        f"正文：\n{temporary_content}"
                    ),
                }
            ],
            config_key=self.MODEL,
            response_model=_LiveEvaluationResult,
            db=db,
            user_id=user_id,
            system_prompt=self._build_live_system_prompt(),
        )

        scores = self._normalize_scores(result.dimension_scores)
        overall_score = result.overall_score
        if overall_score is None:
            overall_score = self._weighted_average(scores)

        return {
            "overall_score": round(float(overall_score), 2),
            "dimension_scores": {k: round(float(v), 2) for k, v in scores.items()},
            "content_type": self.content_type,
            "prompt_version": self.prompt_version,
            "model_used": self.MODEL,
        }

    def _build_system_prompt(self) -> str:
        dims = []
        for key, meta in self.profile["dimensions"].items():
            dims.append(f"- {key}（{meta['label']}）")

        return (
            "你是资深内容评估顾问。"
            f"{self.profile['summary_focus']}\n\n"
            "请按以下维度对文本打分（1-10）：\n"
            + "\n".join(dims)
            + "\n\n输出要求：\n"
            "1. dimension_scores 必须覆盖全部维度，键名必须完全一致。\n"
            "2. summary 用 2-4 句总结这一章的优点和短板。\n"
            "3. suggestions 提供 2-5 条可执行建议，每条包含 dimension/issue/suggestion/priority。\n"
            "4. issue 和 suggestion 必须具体，不要泛泛而谈。\n"
            "5. overall_score 若未给出，将由系统按权重计算。"
        )

    def _build_live_system_prompt(self) -> str:
        dims = "\n".join(f"- {key}" for key in self.dimensions)
        return (
            "你是实时文本评分助手。请快速给出评分，不要输出建议。\n"
            f"内容类型：{self.content_type}\n"
            "只按以下维度打分（1-10）：\n"
            f"{dims}\n\n"
            "输出要求：\n"
            "1. 仅返回 overall_score 和 dimension_scores。\n"
            "2. dimension_scores 必须覆盖全部维度，键名一致。\n"
            "3. 不要输出 summary 和 suggestions。"
        )

    def _normalize_scores(self, raw_scores: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for dimension in self.dimensions:
            value = raw_scores.get(dimension)
            meta = self.profile["dimensions"].get(dimension, {})
            default_score = float(meta.get("default_score", self.DEFAULT_MISSING_SCORE))
            if value is None:
                normalized[dimension] = max(1.0, min(10.0, default_score))
                continue
            try:
                score = float(value)
            except (TypeError, ValueError):
                score = default_score
            normalized[dimension] = max(1.0, min(10.0, score))
        return normalized

    def _weighted_average(self, scores: dict[str, float]) -> float:
        total = 0.0
        for key, weight in self.weights.items():
            total += float(scores.get(key, 0)) * weight
        return total

    def _normalize_suggestions(self, *, suggestions: list[dict], scores: dict[str, float], novel: Novel) -> list[dict]:
        normalized: list[dict] = []
        covered_dimensions: set[str] = set()

        for raw in suggestions:
            dimension = str(raw.get("dimension") or "").strip()
            issue = str(raw.get("issue") or "").strip()
            suggestion = str(raw.get("suggestion") or "").strip()
            if dimension not in self.dimensions or not issue or not suggestion:
                continue

            normalized.append(
                {
                    "dimension": dimension,
                    "issue": issue,
                    "suggestion": suggestion,
                    "priority": self._normalize_priority(str(raw.get("priority") or "medium")),
                    "text_ref": raw.get("text_ref") or self._default_text_ref(dimension=dimension, novel=novel),
                }
            )
            covered_dimensions.add(dimension)

        for dimension, score in sorted(scores.items(), key=lambda item: item[1]):
            if len(normalized) >= 4:
                break
            if dimension in covered_dimensions or float(score) >= 7:
                continue
            normalized.append(self._build_fallback_suggestion(dimension=dimension, novel=novel))

        if not normalized:
            weakest_dimension = min(scores.items(), key=lambda item: item[1])[0]
            normalized.append(self._build_fallback_suggestion(dimension=weakest_dimension, novel=novel))

        return normalized[:4]

    def _build_fallback_suggestion(self, *, dimension: str, novel: Novel) -> dict:
        meta = self.profile["dimensions"].get(dimension, {})
        return {
            "dimension": dimension,
            "issue": meta.get("issue", "这一维度还有明显提升空间。"),
            "suggestion": meta.get("suggestion", "请围绕该维度补强文本表达。"),
            "priority": "high" if dimension == self.dimensions[0] else "medium",
            "text_ref": self._default_text_ref(dimension=dimension, novel=novel),
        }

    def _default_text_ref(self, *, dimension: str, novel: Novel) -> str:
        title = novel.chapter_title or f"第{novel.chapter_index}章"
        if dimension == self.dimensions[0]:
            return f"{title} 开头 10% 内容"
        if dimension == self.dimensions[-1]:
            return f"{title} 结尾 10% 内容"
        return title

    def _normalize_priority(self, priority: str) -> str:
        value = priority.strip().lower()
        if value in {"high", "medium", "low"}:
            return value
        return "medium"

    def _to_eval_fallback_event(self, raw_event: dict) -> dict:
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


def get_evaluator_by_content_type(content_type: str) -> NovelEvaluator:
    return NovelEvaluator(content_type=content_type)

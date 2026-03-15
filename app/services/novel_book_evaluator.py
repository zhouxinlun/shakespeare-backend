from __future__ import annotations

from collections import defaultdict
import logging
from statistics import mean
from typing import Iterable

from app.models.novel import Novel, NovelEvaluation
from app.services.novel_evaluator import EVALUATION_PROFILES

logger = logging.getLogger(__name__)


class NovelBookEvaluator:
    MODEL = "book_evaluator"
    PROMPT_VERSION = "book.v1"

    def __init__(self, content_type: str = "short_drama") -> None:
        profile = EVALUATION_PROFILES.get(content_type) or EVALUATION_PROFILES["general"]
        self.content_type = content_type if content_type in EVALUATION_PROFILES else "general"
        self.profile = profile

    def build_report(
        self,
        *,
        novels: list[Novel],
        evaluations: list[NovelEvaluation],
        focus_areas: set[str] | None = None,
        include_benchmarking: bool = True,
    ) -> dict:
        evaluation_map = {item.novel_id: item for item in evaluations}
        missing_eval_ids = [novel.id for novel in novels if novel.id not in evaluation_map]
        if missing_eval_ids:
            logger.warning(
                "Book evaluation missing chapter evaluations: project_content_type=%s missing=%s",
                self.content_type,
                missing_eval_ids,
            )
            raise ValueError(
                f"缺少 {len(missing_eval_ids)} 章的评估结果，请先完成章节评估后再执行全书评估"
            )

        rows = [
            (novel, evaluation_map[novel.id])
            for novel in sorted(novels, key=lambda item: item.chapter_index)
            if novel.id in evaluation_map
        ]
        if not rows:
            raise ValueError("缺少可用章节评估结果，无法生成全书评估")
        if len(rows) != len(novels):
            raise ValueError("评估章节数与待评估章节数不一致")

        coverage = len(rows) / max(1, len(novels))
        logger.info(
            "Book evaluation coverage: content_type=%s chapters=%s coverage=%.2f%%",
            self.content_type,
            len(rows),
            coverage * 100,
        )

        chapter_scores = [float(evaluation.overall_score) for _, evaluation in rows]
        avg_score = round(mean(chapter_scores), 2)

        dim_values: dict[str, list[float]] = defaultdict(list)
        for _, evaluation in rows:
            for key, value in (evaluation.dimension_scores or {}).items():
                dim_values[str(key)].append(float(value))

        dimension_averages = {
            key: round(mean(values), 2)
            for key, values in sorted(dim_values.items())
            if values
        }

        score_distribution = {
            "excellent": sum(1 for score in chapter_scores if score >= 8.5),
            "good": sum(1 for score in chapter_scores if 7.0 <= score < 8.5),
            "average": sum(1 for score in chapter_scores if 5.5 <= score < 7.0),
            "poor": sum(1 for score in chapter_scores if score < 5.5),
        }

        low_score_chapters = [
            {
                "novel_id": novel.id,
                "chapter_index": novel.chapter_index,
                "chapter_title": novel.chapter_title or f"第{novel.chapter_index}章",
                "overall_score": round(float(evaluation.overall_score), 2),
            }
            for novel, evaluation in rows
            if float(evaluation.overall_score) < 6.5
        ][:8]

        consistency_issues = self._build_consistency_issues(rows)
        if focus_areas:
            consistency_issues = [item for item in consistency_issues if item.get("type") in focus_areas]

        priorities = self._build_improvement_priorities(dimension_averages)
        coherence_score = round(max(0.0, 10 - len(consistency_issues) * 1.2), 2)
        completeness_score = 10.0
        audience_fit_score = round(self._estimate_audience_fit(dimension_averages), 2)

        aggregated_stats = {
            "total_chapters": len(rows),
            "total_words": sum(int(novel.word_count or 0) for novel, _ in rows),
            "total_volumes": len({(novel.volume or "正文").strip() or "正文" for novel, _ in rows}),
            "average_score": avg_score,
            "dimension_averages": dimension_averages,
            "score_distribution": score_distribution,
            "low_score_chapters": low_score_chapters,
        }
        if include_benchmarking:
            aggregated_stats["benchmark"] = self._build_benchmark(avg_score)

        overall_assessment = {
            "overall_score": avg_score,
            "completeness_score": completeness_score,
            "coherence_score": coherence_score,
            "audience_fit_score": audience_fit_score,
            "summary": (
                f"共评估 {len(rows)} 章，平均分 {avg_score}。"
                f"当前识别到 {len(consistency_issues)} 个跨章节风险点，"
                f"建议优先处理 {len(priorities)} 个低分维度。"
            ),
            "improvement_priorities": priorities,
        }

        return {
            "aggregated_stats": aggregated_stats,
            "consistency_issues": consistency_issues,
            "overall_assessment": overall_assessment,
            "evaluated_novel_ids": [novel.id for novel, _ in rows],
            "model_used": self.MODEL,
            "prompt_version": self.PROMPT_VERSION,
        }

    def _build_consistency_issues(self, rows: list[tuple[Novel, NovelEvaluation]]) -> list[dict]:
        issues: list[dict] = []

        # Adjacent chapter score drops often indicate pacing/timeline continuity issues.
        for idx in range(1, len(rows)):
            prev_novel, prev_eval = rows[idx - 1]
            cur_novel, cur_eval = rows[idx]
            delta = float(cur_eval.overall_score) - float(prev_eval.overall_score)
            if delta <= -1.6:
                issues.append(
                    {
                        "type": "timeline",
                        "severity": "high" if delta <= -2.4 else "medium",
                        "title": "章节质量出现明显断层",
                        "description": (
                            f"第{prev_novel.chapter_index}章到第{cur_novel.chapter_index}章评分下降 "
                            f"{abs(round(delta, 2))} 分，叙事衔接和节奏连续性可能存在问题。"
                        ),
                        "affected_chapters": [prev_novel.chapter_index, cur_novel.chapter_index],
                        "suggestion": "检查前后章的动机承接、冲突升级和信息回收，补齐过渡段落。",
                    }
                )

        high_priority_by_chapter = 0
        for novel, evaluation in rows:
            high_count = sum(
                1
                for item in (evaluation.suggestions or [])
                if isinstance(item, dict) and str(item.get("priority", "")).lower() == "high"
            )
            if high_count > 0:
                high_priority_by_chapter += 1
                if high_count >= 2:
                    issues.append(
                        {
                            "type": "character_consistency",
                            "severity": "medium",
                            "title": "单章高优先建议偏多",
                            "description": (
                                f"第{novel.chapter_index}章存在 {high_count} 条高优先级问题，"
                                "建议先完成局部修正再继续后续章节扩写。"
                            ),
                            "affected_chapters": [novel.chapter_index],
                            "suggestion": "优先执行该章高优先级建议，避免问题跨章放大。",
                        }
                    )

        if high_priority_by_chapter >= max(2, len(rows) // 3):
            issues.append(
                {
                    "type": "world_building",
                    "severity": "medium",
                    "title": "整体质量债务累积",
                    "description": (
                        f"有 {high_priority_by_chapter} 章存在高优先级问题，"
                        "说明全书在设定一致性或叙事稳定性上出现系统性风险。"
                    ),
                    "affected_chapters": [novel.chapter_index for novel, _ in rows],
                    "suggestion": "先做一轮全书结构梳理，再逐章修复高优先项。",
                }
            )

        return issues[:12]

    def _build_improvement_priorities(self, dimension_averages: dict[str, float]) -> list[dict]:
        dims_meta = self.profile.get("dimensions", {})
        ranked = sorted(dimension_averages.items(), key=lambda item: item[1])[:3]
        priorities: list[dict] = []
        for key, score in ranked:
            meta = dims_meta.get(key, {})
            priorities.append(
                {
                    "dimension": key,
                    "label": meta.get("label", key),
                    "average_score": round(float(score), 2),
                    "priority": "high" if score < 6.0 else "medium",
                    "recommendation": meta.get("suggestion", "优先提升该维度对应的叙事效果。"),
                }
            )
        return priorities

    def _estimate_audience_fit(self, dimension_averages: dict[str, float]) -> float:
        if not dimension_averages:
            return 0.0
        preferred_keys = self._core_dimension_keys()
        picked_scores: list[float] = []
        for key in preferred_keys:
            if key in dimension_averages:
                picked_scores.append(float(dimension_averages[key]))
        if not picked_scores:
            picked_scores = [float(value) for value in dimension_averages.values()]
        return mean(picked_scores)

    def _build_benchmark(self, avg_score: float) -> dict:
        if avg_score >= 8.5:
            grade = "excellent"
        elif avg_score >= 7.0:
            grade = "good"
        elif avg_score >= 5.5:
            grade = "average"
        else:
            grade = "poor"
        return {
            "grade": grade,
            "level": self._benchmark_label(grade),
        }

    def _core_dimension_keys(self) -> Iterable[str]:
        mapping = {
            "short_drama": ("opening_hook", "cliffhanger_strength", "serialized_drive"),
            "web_novel": ("plot_momentum", "chapter_payoff", "retention_drive"),
            "mystery": ("suspense_setup", "logic_consistency", "reveal_impact"),
            "general": ("plot", "character", "pacing"),
        }
        return mapping.get(self.content_type, ("plot", "character", "pacing"))

    @staticmethod
    def _benchmark_label(grade: str) -> str:
        labels = {
            "excellent": "优秀",
            "good": "良好",
            "average": "中等",
            "poor": "待提升",
        }
        return labels.get(grade, "未知")

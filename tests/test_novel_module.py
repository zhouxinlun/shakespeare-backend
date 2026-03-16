import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.schemas.novel import (
    NovelChatRequest,
    NovelCreate,
    NovelEvaluateBookRequest,
    NovelEvaluateLiveRequest,
    NovelParseRequest,
    NovelUpdate,
)
from app.services.novel_book_evaluator import NovelBookEvaluator
from app.services.novel_chat import recommend_chat_skill
from app.services.novel_evaluator import NovelEvaluator
from app.services.novel_parser import NovelParser


class TestNovelSchemaValidation(unittest.TestCase):
    def test_create_trims_optional_fields(self):
        payload = NovelCreate(
            chapter_index=1,
            volume=" 第一卷 ",
            chapter_title=" 第一章 ",
            content=" 正文内容 ",
        )
        self.assertEqual(payload.volume, "第一卷")
        self.assertEqual(payload.chapter_title, "第一章")
        self.assertEqual(payload.content, "正文内容")

    def test_create_rejects_empty_content(self):
        with self.assertRaises(ValidationError):
            NovelCreate(chapter_index=1, content="   ")

    def test_update_rejects_invalid_chapter_index(self):
        with self.assertRaises(ValidationError):
            NovelUpdate(chapter_index=0)

    def test_parse_request_trims_optional_fields(self):
        payload = NovelParseRequest(
            raw_text=" 正文 ",
            rule_type="separator",
            separator_pattern=" --- ",
            custom_split_rule=" re:\\n---\\n ",
            content_genre=" 悬疑 ",
        )
        self.assertEqual(payload.separator_pattern, "---")
        self.assertEqual(payload.custom_split_rule, "re:\\n---\\n")
        self.assertEqual(payload.content_genre, "悬疑")

    def test_chat_request_validates_message_and_ids(self):
        payload = NovelChatRequest(message=" 请评估第1章 ", skill="chapter_eval", novel_ids=[1, 2])
        self.assertEqual(payload.message, "请评估第1章")
        self.assertEqual(payload.novel_ids, [1, 2])
        with self.assertRaises(ValidationError):
            NovelChatRequest(message="   ")
        with self.assertRaises(ValidationError):
            NovelChatRequest(message="ok", novel_ids=[1, 1])

    def test_live_request_trims_and_rejects_empty_content(self):
        payload = NovelEvaluateLiveRequest(temporary_content=" 临时正文 ", chapter_title=" 第一章 ")
        self.assertEqual(payload.temporary_content, "临时正文")
        self.assertEqual(payload.chapter_title, "第一章")
        with self.assertRaises(ValidationError):
            NovelEvaluateLiveRequest(temporary_content="   ")

    def test_book_request_validates_optional_lists(self):
        payload = NovelEvaluateBookRequest(
            novel_ids=[1, 2],
            focus_areas=[" character_consistency ", "timeline"],
            include_benchmarking=False,
        )
        self.assertEqual(payload.novel_ids, [1, 2])
        self.assertEqual(payload.focus_areas, ["character_consistency", "timeline"])
        self.assertFalse(payload.include_benchmarking)
        with self.assertRaises(ValidationError):
            NovelEvaluateBookRequest(novel_ids=[1, 1])
        with self.assertRaises(ValidationError):
            NovelEvaluateBookRequest(chapters_to_evaluate=[])


class TestNovelChatSkillRecommendation(unittest.TestCase):
    def test_recommend_rewrite_skill(self):
        skill, reason = recommend_chat_skill("请把第3章结尾改写得更有悬念")
        self.assertEqual(skill, "chapter_rewrite")
        self.assertIn("章节改写", reason or "")

    def test_recommend_character_skill(self):
        skill, reason = recommend_chat_skill("分析一下主角和反派的人物关系与动机")
        self.assertEqual(skill, "character_insight")
        self.assertIn("人物", reason or "")

    def test_recommend_none_for_generic_message(self):
        skill, reason = recommend_chat_skill("你好，继续")
        self.assertIsNone(skill)
        self.assertIsNone(reason)


class TestNovelParserRules(unittest.TestCase):
    def test_rule_parse_with_volume_and_chapter(self):
        parser = NovelParser()
        text = """第一卷 初入江湖
第一章 少年出山
风起云涌。

第二章 江湖夜雨
刀光剑影。"""

        chapters = parser._rule_parse(text)  # noqa: SLF001
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["volume"], "第一卷 初入江湖")
        self.assertEqual(chapters[0]["chapter_title"], "第一章 少年出山")
        self.assertIn("风起云涌", chapters[0]["content"])

    def test_rule_parse_without_heading_fallbacks_to_paragraph_chunks(self):
        parser = NovelParser()
        text = "第一段。\n\n第二段。\n\n第三段。"
        chapters = parser._rule_parse(text)  # noqa: SLF001
        self.assertGreaterEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["volume"], "正文")

    def test_rule_parse_paragraph_fallback_uses_smaller_chunks(self):
        parser = NovelParser()
        text = f"{'甲' * 900}\n\n{'乙' * 900}"
        chapters = parser._rule_parse(text)  # noqa: SLF001
        self.assertEqual(len(chapters), 2)

    def test_rule_parse_without_heading_can_return_empty_for_ai_path(self):
        parser = NovelParser()
        text = "第一段。\n第二段。\n第三段。"
        chapters = parser._rule_parse(text, allow_paragraph_fallback=False)  # noqa: SLF001
        self.assertEqual(chapters, [])

    def test_separator_parse_splits_by_marker(self):
        parser = NovelParser()
        text = "第一段内容\n---\n第二段内容\n---\n第三段内容"
        chapters = parser._separator_parse(text, separator_pattern="---")  # noqa: SLF001
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[1]["content"], "第二段内容")

    def test_rhythm_rule_parse_splits_short_story_into_multiple_segments(self):
        parser = NovelParser()
        base = (
            "夜里十点，陈默回到旧宅，发现客厅灯亮着，却没人说话。"
            "他推开书房门，桌上多了一封没有署名的信。"
            "信里只写着一句：真相在地下室。"
            "他刚走到楼梯口，手机突然震动，来电显示是三年前去世的母亲。"
            "电话那头只有呼吸声，然后一句低语：不要下去。"
            "他迟疑了两秒，还是拧开门把，地下室里传来金属拖动的声音。"
        )
        text = base * 4
        analysis = parser._analyze_text(text=text)  # noqa: SLF001
        chapters = parser._rhythm_rule_parse(  # noqa: SLF001
            text,
            analysis=analysis,
            twist_strategy="balanced",
        )
        self.assertGreaterEqual(len(chapters), 2)

    def test_need_rhythm_fallback_triggered_for_mid_short_text(self):
        parser = NovelParser()
        text = (
            "雨夜里她站在站台尽头，手里攥着那张已经褪色的车票。"
            "广播突然报出一串陌生数字，她意识到那正是父亲留下的旧密码。"
            "她追进列车，却在最后一节车厢看见了不该出现的人影。"
        ) * 8
        analysis = parser._analyze_text(text=text)  # noqa: SLF001
        should_fallback = parser._need_rhythm_fallback(  # noqa: SLF001
            chapters=[{"chapter_index": 1, "chapter_title": "第1章", "content": text}],
            text=text,
            analysis=analysis,
        )
        self.assertTrue(should_fallback)

    def test_assess_quality_rejects_single_candidate(self):
        parser = NovelParser()
        quality = parser._assess_quality([  # noqa: SLF001
            {"chapter_title": "第1章", "content": "完整小说正文", "chapter_index": 1}
        ])
        self.assertEqual(quality, "none")

    def test_auto_generated_title_matches_chinese_numerals(self):
        parser = NovelParser()
        self.assertTrue(parser._is_auto_generated_title("第一章"))  # noqa: SLF001
        self.assertTrue(parser._is_auto_generated_title("第12章"))  # noqa: SLF001


class TestNovelEvaluator(unittest.TestCase):
    def test_weighted_average(self):
        evaluator = NovelEvaluator(content_type="short_drama")
        scores = {
            "opening_hook": 8,
            "conflict_density": 7,
            "twist_effectiveness": 6,
            "cliffhanger_strength": 9,
            "visual_adaptability": 7,
            "serialized_drive": 8,
        }
        weighted = evaluator._weighted_average(scores)  # noqa: SLF001
        self.assertAlmostEqual(weighted, 7.54)

    def test_normalize_suggestions_generates_short_drama_fallbacks(self):
        evaluator = NovelEvaluator(content_type="short_drama")
        suggestions = evaluator._normalize_suggestions(  # noqa: SLF001
            suggestions=[],
            scores={
                "opening_hook": 5,
                "conflict_density": 6,
                "twist_effectiveness": 7,
                "cliffhanger_strength": 4,
                "visual_adaptability": 8,
                "serialized_drive": 6,
            },
            novel=SimpleNamespace(chapter_title="第一章", chapter_index=1),
        )
        self.assertGreaterEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["dimension"], "cliffhanger_strength")

    def test_web_novel_profile_uses_expected_dimensions(self):
        evaluator = NovelEvaluator(content_type="web_novel")
        self.assertIn("plot_momentum", evaluator.dimensions)
        self.assertEqual(evaluator.content_type, "web_novel")

    def test_normalize_scores_uses_default_missing_score(self):
        evaluator = NovelEvaluator(content_type="short_drama")
        normalized = evaluator._normalize_scores({"opening_hook": "bad"})  # noqa: SLF001
        self.assertEqual(normalized["opening_hook"], evaluator.DEFAULT_MISSING_SCORE)
        self.assertEqual(normalized["conflict_density"], evaluator.DEFAULT_MISSING_SCORE)


class TestNovelBookEvaluator(unittest.TestCase):
    def test_build_report_outputs_core_sections(self):
        evaluator = NovelBookEvaluator(content_type="short_drama")
        novels = [
            SimpleNamespace(id=1, chapter_index=1, chapter_title="第1章", volume="正文", word_count=1200),
            SimpleNamespace(id=2, chapter_index=2, chapter_title="第2章", volume="正文", word_count=1100),
        ]
        evaluations = [
            SimpleNamespace(
                novel_id=1,
                overall_score=7.8,
                dimension_scores={"opening_hook": 8, "cliffhanger_strength": 7.5, "serialized_drive": 7.2},
                suggestions=[{"priority": "medium"}],
            ),
            SimpleNamespace(
                novel_id=2,
                overall_score=6.1,
                dimension_scores={"opening_hook": 6.2, "cliffhanger_strength": 5.6, "serialized_drive": 6.0},
                suggestions=[{"priority": "high"}, {"priority": "high"}],
            ),
        ]

        report = evaluator.build_report(novels=novels, evaluations=evaluations, include_benchmarking=True)
        self.assertIn("aggregated_stats", report)
        self.assertIn("consistency_issues", report)
        self.assertIn("overall_assessment", report)
        self.assertEqual(report["aggregated_stats"]["total_chapters"], 2)
        self.assertAlmostEqual(report["overall_assessment"]["overall_score"], 6.95)
        self.assertGreaterEqual(len(report["overall_assessment"]["improvement_priorities"]), 1)

    def test_build_report_rejects_missing_evaluations(self):
        evaluator = NovelBookEvaluator(content_type="short_drama")
        novels = [SimpleNamespace(id=1, chapter_index=1, chapter_title="第1章", volume="正文", word_count=1200)]
        with self.assertRaises(ValueError):
            evaluator.build_report(novels=novels, evaluations=[])


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.setting import (
    _test_image_connectivity,
    _test_video_connectivity,
    _probe_vision,
    _test_auto_connectivity,
    _infer_manufacturer,
    _infer_probe_order,
    _infer_auto_probe_budget,
    _volcengine_video_endpoint,
    _resolve_manufacturer,
    _can_continue_probe,
)
from app.services.llm import normalize_api_key, apply_provider_kwargs, normalize_openai_compatible_base_url


class TestApiKeyNormalization(unittest.TestCase):
    def test_keep_raw_key_when_missing_bearer(self):
        self.assertEqual(normalize_api_key("sk-abc"), "sk-abc")

    def test_strip_bearer_when_present(self):
        self.assertEqual(normalize_api_key("Bearer sk-abc"), "sk-abc")
        self.assertEqual(normalize_api_key("bearer   sk-abc"), "sk-abc")


class TestProviderKwargs(unittest.TestCase):
    def test_openai_compatible_provider_has_custom_provider_and_dual_base(self):
        kwargs = {"model": "openai/qwen-plus", "api_key": "sk-abc"}
        out = apply_provider_kwargs(kwargs, "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(out["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(out["api_base"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(out["custom_llm_provider"], "openai")

    def test_non_openai_compatible_provider_without_base_url(self):
        kwargs = {"model": "anthropic/claude-3-5-sonnet", "api_key": "sk-abc"}
        out = apply_provider_kwargs(kwargs, "anthropic", None)
        self.assertNotIn("base_url", out)
        self.assertNotIn("api_base", out)
        self.assertNotIn("custom_llm_provider", out)

    def test_neuxnet_uses_openai_compatible_strategy(self):
        kwargs = {"model": "openai/qwen3.5-plus", "api_key": "sk-abc"}
        out = apply_provider_kwargs(kwargs, "neuxnet", "https://tokenhub.neuxnet.com/v1")
        self.assertEqual(out["base_url"], "https://tokenhub.neuxnet.com/v1")
        self.assertEqual(out["api_base"], "https://tokenhub.neuxnet.com/v1")
        self.assertEqual(out["custom_llm_provider"], "openai")

    def test_volcengine_image_endpoint_is_normalized(self):
        kwargs = {"model": "openai/doubao-seedream-4-5-251128", "api_key": "sk-abc"}
        out = apply_provider_kwargs(
            kwargs,
            "volcengine",
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        )
        self.assertEqual(out["base_url"], "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(out["api_base"], "https://ark.cn-beijing.volces.com/api/v3")


class TestBaseURLNormalize(unittest.TestCase):
    def test_strip_known_endpoint_suffix(self):
        out = normalize_openai_compatible_base_url("https://api.example.com/v1/chat/completions")
        self.assertEqual(out, "https://api.example.com/v1")

    def test_keep_gateway_root_when_already_clean(self):
        out = normalize_openai_compatible_base_url("https://api.example.com/v1")
        self.assertEqual(out, "https://api.example.com/v1")

    def test_add_scheme_when_missing(self):
        out = normalize_openai_compatible_base_url("api.example.com/v1/images/generations")
        self.assertEqual(out, "https://api.example.com/v1")


class TestVolcengineVideoEndpoint(unittest.TestCase):
    def test_keep_task_endpoint_when_already_full(self):
        out = _volcengine_video_endpoint("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")
        self.assertEqual(out, "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")

    def test_append_task_endpoint_when_base_is_root(self):
        out = _volcengine_video_endpoint("https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(out, "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")

    def test_strip_invalid_videos_suffix_to_task_endpoint(self):
        out = _volcengine_video_endpoint("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/videos")
        self.assertEqual(out, "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")

    def test_keep_task_endpoint_for_custom_gateway_host(self):
        out = _volcengine_video_endpoint("https://gateway.example.com/api/v3/contents/generations/tasks")
        self.assertEqual(out, "https://gateway.example.com/api/v3/contents/generations/tasks")


class TestManufacturerInference(unittest.TestCase):
    def test_infer_qwen_by_aliyun_host_and_model_hint(self):
        out = _infer_manufacturer(manufacturer=None, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="qwen-plus")
        self.assertEqual(out, "qwen")

    def test_infer_other_for_aliyun_host_without_model_hint(self):
        out = _infer_manufacturer(manufacturer=None, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="custom-model")
        self.assertEqual(out, "other")

    def test_infer_openai_by_model_prefix(self):
        out = _infer_manufacturer(manufacturer=None, base_url=None, model="openai/gpt-4o-mini")
        self.assertEqual(out, "openai")

    def test_base_url_has_higher_priority_than_provided_manufacturer(self):
        out = _infer_manufacturer(
            manufacturer="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
        )
        self.assertEqual(out, "qwen")

    def test_base_url_without_scheme_can_be_parsed(self):
        out = _infer_manufacturer(manufacturer=None, base_url="api.openai.com/v1", model="gpt-4o-mini")
        self.assertEqual(out, "openai")

    def test_infer_neuxnet_by_host(self):
        out = _infer_manufacturer(manufacturer=None, base_url="https://tokenhub.neuxnet.com/v1", model="qwen3.5-plus")
        self.assertEqual(out, "neuxnet")

    def test_keep_provided_manufacturer(self):
        out = _infer_manufacturer(manufacturer="deepseek", base_url=None, model="gpt-4o")
        self.assertEqual(out, "deepseek")


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class TestManufacturerResolve(unittest.IsolatedAsyncioTestCase):
    async def test_mapping_prefix_has_highest_priority(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_FakeScalarResult(
                [
                    SimpleNamespace(manufacturer="openai", base_url_prefix="https://dashscope.aliyuncs.com/"),
                    SimpleNamespace(manufacturer="qwen", base_url_prefix="https://dashscope.aliyuncs.com/compatible-mode/v1"),
                ]
            )
        )

        out = await _resolve_manufacturer(
            db=db,
            user_id=1,
            manufacturer="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            model="gpt-4o-mini",
        )
        self.assertEqual(out, "qwen")

    async def test_mapping_can_match_without_scheme(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_FakeScalarResult(
                [
                    SimpleNamespace(manufacturer="openai", base_url_prefix="api.openai.com/v1"),
                ]
            )
        )

        out = await _resolve_manufacturer(
            db=db,
            user_id=1,
            manufacturer=None,
            base_url="https://api.openai.com/v1/responses",
            model="custom-model",
        )
        self.assertEqual(out, "openai")

    async def test_fallback_to_inference_when_mapping_not_hit(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_FakeScalarResult([]))

        out = await _resolve_manufacturer(
            db=db,
            user_id=1,
            manufacturer=None,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus-latest",
        )
        self.assertEqual(out, "qwen")


class TestAutoProbeDecision(unittest.TestCase):
    def test_authentication_error_is_hard_stop(self):
        self.assertFalse(_can_continue_probe("AuthenticationError: Incorrect API key", "text"))

    def test_not_found_can_continue(self):
        self.assertTrue(_can_continue_probe("NotFoundError: Error code: 404", "image"))

    def test_not_found_without_404_can_continue(self):
        self.assertTrue(_can_continue_probe("litellm.NotFoundError: OpenAIException -", "video"))

    def test_invalid_action_can_continue(self):
        self.assertTrue(_can_continue_probe("The specified action is invalid: /api/v3/images/generations/chat/completions", "text"))

    def test_seedream_prefers_image_probe(self):
        self.assertEqual(_infer_probe_order("doubao-seedream-4-5-251128")[0], "image")

    def test_image_endpoint_prefers_image_probe(self):
        self.assertEqual(
            _infer_probe_order("unknown-model", base_url="https://foo.bar/v1/images/generations")[0],
            "image",
        )

    def test_image_endpoint_budget_is_single_probe(self):
        budget, reason = _infer_auto_probe_budget(
            "unknown-model",
            base_url="https://foo.bar/v1/images/generations",
        )
        self.assertEqual(budget, 1)
        self.assertEqual(reason, "endpoint-image")

    def test_seedream_budget_is_single_probe(self):
        budget, reason = _infer_auto_probe_budget("doubao-seedream-4-5-251128")
        self.assertEqual(budget, 1)
        self.assertEqual(reason, "model-image")


class TestImageCapability(unittest.IsolatedAsyncioTestCase):
    async def test_image_pass_with_url(self):
        with patch("litellm.aimage_generation", new=AsyncMock(return_value={"data": [{"url": "https://img.example/1.png"}]})):
            result = await _test_image_connectivity(
                {"model": "openai/qwen-image-edit-plus", "api_key": "Bearer sk-demo"}
            )
        self.assertTrue(result["supports_image_generation"])
        self.assertIn("图片生成成功", result["reply"])

    async def test_image_pass_with_b64(self):
        with patch("litellm.aimage_generation", new=AsyncMock(return_value={"data": [{"b64_json": "ZmFrZS1iNjQ="}]})):
            result = await _test_image_connectivity(
                {"model": "openai/qwen-image-edit-plus", "api_key": "Bearer sk-demo"}
            )
        self.assertTrue(result["supports_image_generation"])
        self.assertIn("b64", result["reply"])

    async def test_image_fail_without_payload(self):
        with patch("litellm.aimage_generation", new=AsyncMock(return_value={"data": [{}]})):
            with self.assertRaises(ValueError):
                await _test_image_connectivity(
                    {"model": "openai/qwen-image-edit-plus", "api_key": "Bearer sk-demo"}
                )

    async def test_seedream_starts_from_unified_small_size(self):
        mock_fn = AsyncMock(return_value={"data": [{"url": "https://img.example/seedream.png"}]})
        with patch("litellm.aimage_generation", new=mock_fn):
            result = await _test_image_connectivity(
                {"model": "openai/doubao-seedream-4-5-251128", "api_key": "sk-demo"}
            )
        self.assertTrue(result["supports_image_generation"])
        self.assertIn("size=512x512", result["reply"])
        self.assertEqual(mock_fn.await_args.kwargs["size"], "512x512")
        self.assertIsNone(result["image_min_size"])

    async def test_image_retries_with_adaptive_size_from_error(self):
        mock_fn = AsyncMock(
            side_effect=[
                RuntimeError("The parameter `size` specified in the request is not valid: image size must be at least 3686400 pixels."),
                {"data": [{"url": "https://img.example/retry.png"}]},
            ]
        )
        with patch("litellm.aimage_generation", new=mock_fn):
            result = await _test_image_connectivity(
                {"model": "openai/custom-image-model", "api_key": "sk-demo"}
            )
        self.assertTrue(result["supports_image_generation"])
        self.assertIn("size=1920x1920", result["reply"])
        self.assertEqual(result["image_min_size"], "1920x1920")
        self.assertEqual(mock_fn.await_count, 2)


class TestVideoCapability(unittest.IsolatedAsyncioTestCase):
    async def test_video_pass_with_video_id(self):
        with patch("litellm.avideo_generation", new=AsyncMock(return_value={"id": "video_task_123", "status": "queued"})):
            with patch("litellm.avideo_status", new=AsyncMock(return_value={"status": "processing"})):
                result = await _test_video_connectivity(
                    {"model": "openai/wanx2.1-video", "api_key": "Bearer sk-demo"}
                )
        self.assertTrue(result["supports_video_generation"])
        self.assertIn("视频提交通过", result["reply"])

    async def test_video_pass_with_inline_videos(self):
        with patch("litellm.avideo_generation", new=AsyncMock(return_value={"videos": [{"url": "https://video.example/1.mp4"}]})):
            result = await _test_video_connectivity(
                {"model": "openai/wanx2.1-video", "api_key": "Bearer sk-demo"}
            )
        self.assertTrue(result["supports_video_generation"])
        self.assertIn("视频提交通过", result["reply"])

    async def test_video_fail_without_identifier_status_or_videos(self):
        with patch("litellm.avideo_generation", new=AsyncMock(return_value={"foo": "bar"})):
            with self.assertRaises(ValueError):
                await _test_video_connectivity(
                    {"model": "openai/wanx2.1-video", "api_key": "Bearer sk-demo"}
                )

    async def test_video_volcengine_task_api_probe(self):
        class MockResp:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = '{"id":"task_123","status":"queued"}'

            @staticmethod
            def json():
                return {"id": "task_123", "status": "queued"}

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=MockResp())):
            result = await _test_video_connectivity(
                {
                    "model": "openai/doubao-seedance-1-5-pro-251215",
                    "api_key": "sk-demo",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
                }
            )
        self.assertTrue(result["supports_video_generation"])
        self.assertIn("volcengine-task", result["reply"])

    async def test_video_volcengine_task_probe_uses_minimal_prompt(self):
        class MockResp:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = '{"id":"task_123","status":"queued"}'

            @staticmethod
            def json():
                return {"id": "task_123", "status": "queued"}

        captured_payloads = []

        async def _mock_post(*args, **kwargs):
            captured_payloads.append(kwargs.get("json"))
            return MockResp()

        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_mock_post)):
            await _test_video_connectivity(
                {
                    "model": "openai/doubao-seedance-1-5-pro-251215",
                    "api_key": "sk-demo",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
                }
            )

        self.assertTrue(captured_payloads)
        first_text = captured_payloads[0]["content"][0]["text"]
        self.assertNotIn("--duration", first_text)
        self.assertNotIn("--watermark", first_text)

    async def test_video_volcengine_task_api_requires_key(self):
        with self.assertRaises(ValueError) as cm:
            await _test_video_connectivity(
                {
                    "model": "openai/doubao-seedance-1-5-pro-251215",
                    "api_key": "",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
                }
            )
        self.assertIn("API Key 为空", str(cm.exception))


class TestVisionCapability(unittest.IsolatedAsyncioTestCase):
    async def test_vision_pass_with_text_content(self):
        mock_res = type("R", (), {})()
        mock_choice = type("C", (), {})()
        mock_msg = type("M", (), {})()
        mock_msg.content = [{"type": "text", "text": "这是一张测试图片。"}]
        mock_choice.message = mock_msg
        mock_res.choices = [mock_choice]
        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_res)):
            ok, summary = await _probe_vision({"model": "openai/qwen2.5-vl", "api_key": "sk-demo"})
        self.assertTrue(ok)
        self.assertIn("Vision 支持", summary)

    async def test_vision_fail_without_text_output(self):
        mock_res = type("R", (), {})()
        mock_choice = type("C", (), {})()
        mock_msg = type("M", (), {})()
        mock_msg.content = [{"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}]
        mock_choice.message = mock_msg
        mock_res.choices = [mock_choice]
        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_res)):
            ok, summary = await _probe_vision({"model": "openai/qwen2.5-vl", "api_key": "sk-demo"})
        self.assertFalse(ok)
        self.assertIn("Vision 失败", summary)

    async def test_vision_fail_with_refusal_text(self):
        mock_res = type("R", (), {})()
        mock_choice = type("C", (), {})()
        mock_msg = type("M", (), {})()
        mock_msg.content = "我无法查看或分析图片，因此无法描述其内容。"
        mock_choice.message = mock_msg
        mock_res.choices = [mock_choice]
        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_res)):
            ok, summary = await _probe_vision({"model": "openai/qwen-plus-latest", "api_key": "sk-demo"})
        self.assertFalse(ok)
        self.assertIn("拒绝查看图片", summary)


class TestAutoDetection(unittest.IsolatedAsyncioTestCase):
    async def test_auto_detects_image_when_image_probe_passes(self):
        with patch(
            "app.api.setting._test_image_connectivity",
            new=AsyncMock(
                return_value={
                    "reply": "图片生成成功",
                    "detected_type": "image",
                    "supports_tools": None,
                    "supports_thinking": None,
                    "supports_vision": None,
                    "supports_image_generation": True,
                    "supports_video_generation": None,
                }
            ),
        ):
            result = await _test_auto_connectivity(
                {"model": "openai/qwen-image", "api_key": "sk-demo"},
                prompt="Reply with OK.",
                raw_model="qwen-image",
            )
        self.assertEqual(result["detected_type"], "image")
        self.assertTrue(result["supports_image_generation"])
        self.assertIsNone(result["supports_video_generation"])

    async def test_auto_fails_when_all_primary_capabilities_fail(self):
        with patch("app.api.setting._test_text_connectivity", new=AsyncMock(side_effect=ValueError("text unsupported 404"))):
            with patch("app.api.setting._test_image_connectivity", new=AsyncMock(side_effect=ValueError("image unsupported 404"))):
                with patch("app.api.setting._test_video_connectivity", new=AsyncMock(side_effect=ValueError("video unsupported 404"))):
                    with self.assertRaises(ValueError):
                        await _test_auto_connectivity(
                            {"model": "openai/unknown", "api_key": "sk-demo"},
                            prompt="Reply with OK.",
                            raw_model="unknown",
                        )


if __name__ == "__main__":
    unittest.main()

import unittest
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from app.services.llm import AllModelsExhaustedError, call_llm_stream, call_llm_structured


class _FakeScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, map_entry, configs):
        self._map_entry = map_entry
        self._configs = configs

    async def scalar(self, _stmt):
        return self._map_entry

    async def execute(self, _stmt):
        return _FakeScalarRows(self._configs)


class _RateLimitError(Exception):
    status_code = 429


class _UnauthorizedError(Exception):
    status_code = 401


def _stream_response(chunks: list[str]):
    class _Response:
        def __aiter__(self):
            async def _gen():
                for text in chunks:
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

            return _gen()

    return _Response()


def _stream_response_with_error(chunks: list[str], error: Exception):
    class _Response:
        def __aiter__(self):
            async def _gen():
                for text in chunks:
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
                raise error

            return _gen()

    return _Response()


def _structured_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class TestLLMFallback(unittest.IsolatedAsyncioTestCase):
    async def test_stream_falls_back_to_global_text_strategy_when_map_missing(self):
        now = datetime.now(timezone.utc)
        db = _FakeDB(
            map_entry=None,
            configs=[
                SimpleNamespace(
                    id=1,
                    type="text",
                    manufacturer="openai",
                    model="gpt-4o-mini",
                    api_key="sk-1",
                    base_url=None,
                    last_test_status="passed",
                    last_tested_at=now,
                    created_at=now,
                ),
                SimpleNamespace(
                    id=2,
                    type="text",
                    manufacturer="anthropic",
                    model="claude-3-5-sonnet",
                    api_key="sk-2",
                    base_url=None,
                    last_test_status=None,
                    last_tested_at=now - timedelta(hours=1),
                    created_at=now - timedelta(hours=1),
                ),
            ],
        )

        mock_acompletion = AsyncMock(return_value=_stream_response(["ok"]))
        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            chunks: list[str] = []
            async for item in call_llm_stream(
                messages=[{"role": "user", "content": "hi"}],
                config_key="novel_evaluator",
                db=db,  # type: ignore[arg-type]
                user_id=1,
            ):
                if isinstance(item, str):
                    chunks.append(item)

        self.assertEqual("".join(chunks), "ok")
        kwargs = mock_acompletion.await_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-4o-mini")

    async def test_stream_passes_user_id_to_config_lookup(self):
        db = object()
        mock_get_configs = AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=1,
                    manufacturer="openai",
                    model="gpt-4o",
                    api_key="sk-1",
                    base_url=None,
                )
            ]
        )
        mock_acompletion = AsyncMock(return_value=_stream_response(["ok"]))

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch("app.services.llm._get_configs", mock_get_configs):
            with patch.dict(sys.modules, {"litellm": fake_litellm}):
                chunks: list[str] = []
                async for item in call_llm_stream(
                    messages=[{"role": "user", "content": "hi"}],
                    config_key="outlineScriptAgent",
                    db=db,  # type: ignore[arg-type]
                    user_id=99,
                ):
                    if isinstance(item, str):
                        chunks.append(item)

        self.assertEqual("".join(chunks), "ok")
        mock_get_configs.assert_awaited_once_with("outlineScriptAgent", db, user_id=99)

    async def test_stream_switches_to_fallback_and_emits_warning_event(self):
        db = _FakeDB(
            map_entry=SimpleNamespace(config_id=1, fallback_config_ids=[2]),
            configs=[
                SimpleNamespace(id=1, manufacturer="openai", model="gpt-4o", api_key="sk-1", base_url=None),
                SimpleNamespace(id=2, manufacturer="anthropic", model="claude-3-5-sonnet", api_key="sk-2", base_url=None),
            ],
        )

        mock_acompletion = AsyncMock(
            side_effect=[
                _RateLimitError("quota exceeded"),
                _stream_response(["hello ", "world"]),
            ]
        )

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            outputs: list[dict | str] = []
            async for item in call_llm_stream(
                messages=[{"role": "user", "content": "hi"}],
                config_key="outlineScriptAgent",
                db=db,  # type: ignore[arg-type]
                user_id=1,
            ):
                outputs.append(item)

        self.assertTrue(outputs)
        self.assertIsInstance(outputs[0], dict)
        self.assertEqual(outputs[0]["type"], "fallback_warning")  # type: ignore[index]
        self.assertFalse(outputs[0]["reset_content"])  # type: ignore[index]
        self.assertEqual("".join(v for v in outputs if isinstance(v, str)), "hello world")
        self.assertEqual(mock_acompletion.await_count, 2)

    async def test_stream_marks_reset_content_when_error_happens_after_partial_output(self):
        db = _FakeDB(
            map_entry=SimpleNamespace(config_id=1, fallback_config_ids=[2]),
            configs=[
                SimpleNamespace(id=1, manufacturer="openai", model="gpt-4o", api_key="sk-1", base_url=None),
                SimpleNamespace(id=2, manufacturer="anthropic", model="claude-3-5-sonnet", api_key="sk-2", base_url=None),
            ],
        )
        first_error = _RateLimitError("midstream timeout")
        mock_acompletion = AsyncMock(
            side_effect=[
                _stream_response_with_error(["partial"], first_error),
                _stream_response(["full"]),
            ]
        )

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            outputs: list[dict | str] = []
            async for item in call_llm_stream(
                messages=[{"role": "user", "content": "hi"}],
                config_key="outlineScriptAgent",
                db=db,  # type: ignore[arg-type]
                user_id=1,
            ):
                outputs.append(item)

        warning = next(v for v in outputs if isinstance(v, dict) and v.get("type") == "fallback_warning")
        self.assertTrue(warning["reset_content"])

    async def test_stream_does_not_fallback_on_unauthorized(self):
        db = _FakeDB(
            map_entry=SimpleNamespace(config_id=1, fallback_config_ids=[2]),
            configs=[
                SimpleNamespace(id=1, manufacturer="openai", model="gpt-4o", api_key="sk-1", base_url=None),
                SimpleNamespace(id=2, manufacturer="anthropic", model="claude-3-5-sonnet", api_key="sk-2", base_url=None),
            ],
        )

        mock_acompletion = AsyncMock(
            side_effect=[
                _UnauthorizedError("incorrect api key"),
                _stream_response(["should-not-reach"]),
            ]
        )

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            with self.assertRaises(_UnauthorizedError):
                async for _ in call_llm_stream(
                    messages=[{"role": "user", "content": "hi"}],
                    config_key="outlineScriptAgent",
                    db=db,  # type: ignore[arg-type]
                    user_id=1,
                ):
                    pass

        self.assertEqual(mock_acompletion.await_count, 1)

    async def test_stream_raises_all_models_exhausted_when_chain_fails(self):
        db = _FakeDB(
            map_entry=SimpleNamespace(config_id=1, fallback_config_ids=[2]),
            configs=[
                SimpleNamespace(id=1, manufacturer="openai", model="gpt-4o", api_key="sk-1", base_url=None),
                SimpleNamespace(id=2, manufacturer="anthropic", model="claude-3-5-sonnet", api_key="sk-2", base_url=None),
            ],
        )

        mock_acompletion = AsyncMock(
            side_effect=[
                _RateLimitError("quota exceeded"),
                _RateLimitError("still quota exceeded"),
            ]
        )

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            with self.assertRaises(AllModelsExhaustedError):
                async for _ in call_llm_stream(
                    messages=[{"role": "user", "content": "hi"}],
                    config_key="outlineScriptAgent",
                    db=db,  # type: ignore[arg-type]
                    user_id=1,
                ):
                    pass

        self.assertEqual(mock_acompletion.await_count, 2)

    async def test_structured_fallback_on_json_decode_error_with_callback(self):
        class _Resp(BaseModel):
            title: str

        db = _FakeDB(
            map_entry=SimpleNamespace(config_id=1, fallback_config_ids=[2]),
            configs=[
                SimpleNamespace(id=1, manufacturer="openai", model="gpt-4o", api_key="sk-1", base_url=None),
                SimpleNamespace(id=2, manufacturer="anthropic", model="claude-3-5-sonnet", api_key="sk-2", base_url=None),
            ],
        )
        mock_acompletion = AsyncMock(
            side_effect=[
                _structured_response("not-json"),
                _structured_response('{"title":"ok"}'),
            ]
        )
        fallback_events: list[dict] = []

        async def _on_fallback(event: dict):
            fallback_events.append(event)

        fake_litellm = ModuleType("litellm")
        fake_litellm.acompletion = mock_acompletion
        with patch.dict(sys.modules, {"litellm": fake_litellm}):
            result = await call_llm_structured(
                messages=[{"role": "user", "content": "hi"}],
                config_key="outlineScriptAgent",
                response_model=_Resp,
                db=db,  # type: ignore[arg-type]
                user_id=1,
                on_fallback=_on_fallback,
            )

        self.assertEqual(result.title, "ok")
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(fallback_events[0]["type"], "fallback_warning")

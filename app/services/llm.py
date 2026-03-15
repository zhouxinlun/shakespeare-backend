"""
LLM 服务层 - 封装 liteLLM，支持多 provider
从数据库读取 AI 配置，按 key 查询对应模型
"""
import json
import logging
from urllib.parse import urlparse, urlunparse
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Optional, Type, TypeVar, TypedDict

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import AIConfig, AIModelMap

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class FallbackWarningEvent(TypedDict):
    type: Literal["fallback_warning"]
    key: str
    from_model: str
    to_model: str
    reason: str
    message: str
    reset_content: bool


FallbackCallback = Callable[[FallbackWarningEvent], Optional[Awaitable[None]]]


class AllModelsExhaustedError(RuntimeError):
    def __init__(self, key: str, attempted_configs: list[AIConfig], last_error: Exception):
        attempted = " -> ".join(c.model for c in attempted_configs) or "unknown"
        super().__init__(f"模型链全部失败：{key}（{attempted}）。最后错误：{last_error}")


# manufacturer -> liteLLM model prefix 映射
MANUFACTURER_PREFIX = {
    "openai": "openai/",
    "anthropic": "anthropic/",         # claude-3-5-sonnet -> anthropic/claude-3-5-sonnet
    "deepseek": "deepseek/",
    "gemini": "gemini/",
    "xai": "xai/",
    "qwen": "openai/",                 # qwen 走 openai-compatible
    "neuxnet": "openai/",              # neuxnet 走 openai-compatible
    "zhipu": "openai/",
    "volcengine": "openai/",           # 豆包走 openai-compatible baseURL
    "other": "openai/",
}

FALLBACK_STATUS_CODES = {408, 429, 502, 503, 504}
NON_FALLBACK_STATUS_CODES = {400, 401}
NON_FALLBACK_HINTS = (
    "bad request",
    "unauthorized",
    "incorrect api key",
    "invalid api key",
    "authentication",
)
FALLBACK_HINTS = (
    "rate limit",
    "quota",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "timeout",
    "timed out",
    "connection error",
    "connection aborted",
    "connection reset",
)


async def _get_configs(key: str, db: AsyncSession, *, user_id: int) -> list[AIConfig]:
    """根据 agent key 从数据库查询完整模型链（主模型 + fallback）"""
    map_entry = await db.scalar(select(AIModelMap).where(AIModelMap.key == key))
    if not map_entry or not map_entry.config_id:
        return []

    ordered_ids: list[int] = []
    seen: set[int] = set()

    for raw_id in [map_entry.config_id, *(map_entry.fallback_config_ids or [])]:
        if not isinstance(raw_id, int):
            continue
        if raw_id in seen:
            continue
        ordered_ids.append(raw_id)
        seen.add(raw_id)

    if not ordered_ids:
        return []

    result = await db.execute(
        select(AIConfig).where(
            AIConfig.id.in_(ordered_ids),
            AIConfig.user_id == user_id,
        )
    )
    config_rows = result.scalars().all()
    config_by_id = {c.id: c for c in config_rows}
    return [config_by_id[cid] for cid in ordered_ids if cid in config_by_id]


def _build_model_string(config: AIConfig) -> str:
    """将数据库配置转换为 liteLLM model string"""
    return build_model_string(config.manufacturer, config.model)


def build_model_string(manufacturer: str, model: str) -> str:
    """
    构造 liteLLM 模型名：
    - 若用户已传 provider/model（包含 /），直接使用，避免双前缀
    - 若仅传裸模型名，则按 manufacturer 自动补前缀
    """
    model_name = (model or "").strip()
    if not model_name:
        return model_name
    if "/" in model_name:
        return model_name
    prefix = MANUFACTURER_PREFIX.get(manufacturer, "openai/")
    return f"{prefix}{model_name}"


def normalize_api_key(api_key: str) -> str:
    """
    统一清洗 API Key（请求发送前）：
    - 去除首尾空格
    - 若用户输入了 `Bearer xxx`，自动提取成裸 key `xxx`
    - 若本身就是裸 key，保持不变
    """
    key = (api_key or "").strip()
    if not key:
        return key
    if key.lower().startswith("bearer "):
        return key[7:].strip()
    return key


def normalize_openai_compatible_base_url(base_url: Optional[str]) -> Optional[str]:
    """
    兼容用户把具体 endpoint 填到 base_url 的情况，例如：
    - /chat/completions
    - /images/generations
    - /videos/generations
    自动裁剪为网关根路径，避免被拼接成无效 action。
    """
    raw = (base_url or "").strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return raw.rstrip("/")

    path = (parsed.path or "").rstrip("/")
    lower_path = path.lower()
    endpoint_suffixes = [
        "/chat/completions",
        "/responses",
        "/completions",
        "/embeddings",
        "/images/generations",
        "/videos/generations",
        "/audio/speech",
        "/audio/transcriptions",
    ]
    for suffix in endpoint_suffixes:
        if lower_path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    normalized = urlunparse((parsed.scheme or "https", parsed.netloc, path.rstrip("/"), "", "", ""))
    return normalized.rstrip("/")


def apply_provider_kwargs(kwargs: dict, manufacturer: str, base_url: Optional[str]) -> dict:
    """
    补充 provider 相关参数，避免 openai-compatible 网关被错误路由到官方 OpenAI 域名。
    """
    openai_compatible = {"openai", "qwen", "neuxnet", "zhipu", "volcengine", "other"}
    normalized_base_url = base_url
    if manufacturer in openai_compatible:
        normalized_base_url = normalize_openai_compatible_base_url(base_url)

    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url
        kwargs["api_base"] = normalized_base_url
    if manufacturer in openai_compatible:
        kwargs["custom_llm_provider"] = "openai"
    return kwargs


def _extract_status_code(error: Exception) -> Optional[int]:
    attrs = ("status_code", "status", "http_status", "code")
    for attr in attrs:
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    response = getattr(error, "response", None)
    if response is not None:
        for attr in attrs:
            value = getattr(response, attr, None)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)

    return None


def _is_fallbackable_error(error: Exception) -> bool:
    status_code = _extract_status_code(error)
    if status_code in NON_FALLBACK_STATUS_CODES:
        return False
    if status_code in FALLBACK_STATUS_CODES:
        return True

    err_name = type(error).__name__.lower()
    err_msg = str(error).lower()

    if any(hint in err_name for hint in ("badrequest", "authentication", "unauthorized")):
        return False
    if any(hint in err_msg for hint in NON_FALLBACK_HINTS):
        return False

    if any(hint in err_name for hint in ("ratelimit", "serviceunavailable", "timeout", "apiconnection")):
        return True
    return any(hint in err_msg for hint in FALLBACK_HINTS)


def build_fallback_event(
    *,
    key: str,
    from_model: str,
    to_model: str,
    reason: str,
    reset_content: bool = False,
) -> FallbackWarningEvent:
    event: FallbackWarningEvent = {
        "type": "fallback_warning",
        "key": key,
        "from_model": from_model,
        "to_model": to_model,
        "reason": reason,
        "message": f"{key} 主模型 {from_model} 不可用，已自动切换至 {to_model}",
        "reset_content": reset_content,
    }
    logger.warning(
        "LLM fallback: key=%s from=%s to=%s reset_content=%s reason=%s",
        key,
        from_model,
        to_model,
        reset_content,
        reason,
    )
    return event


async def _dispatch_fallback_event(
    event: FallbackWarningEvent,
    on_fallback: Optional[FallbackCallback],
) -> None:
    if not on_fallback:
        return
    maybe_awaitable = on_fallback(event)
    if maybe_awaitable is not None:
        await maybe_awaitable


def _build_completion_kwargs(
    *,
    config: AIConfig,
    messages: list[dict],
    stream: bool,
    response_format: Optional[dict] = None,
) -> dict:
    kwargs = {
        "model": _build_model_string(config),
        "messages": messages,
        "stream": stream,
        "api_key": normalize_api_key(config.api_key),
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    return apply_provider_kwargs(kwargs, config.manufacturer, config.base_url)


def _extract_delta_content(chunk: Any) -> Optional[str]:
    choices = getattr(chunk, "choices", None)
    if choices is None and isinstance(chunk, dict):
        choices = chunk.get("choices")
    if not choices:
        return None

    first = choices[0]
    delta = getattr(first, "delta", None)
    if delta is None and isinstance(first, dict):
        delta = first.get("delta")
    if delta is None:
        return None

    if isinstance(delta, dict):
        content = delta.get("content")
    else:
        content = getattr(delta, "content", None)
    return content if isinstance(content, str) else None


def _extract_response_message_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise ValueError("LLM 返回内容为空")

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")

    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if not isinstance(content, str):
        raise ValueError("LLM 返回内容不是有效文本")
    return content


async def call_llm_stream(
    messages: list[dict],
    config_key: str,
    db: AsyncSession,
    user_id: int,
    system_prompt: Optional[str] = None,
    on_fallback: Optional[FallbackCallback] = None,
) -> AsyncIterator[str | FallbackWarningEvent]:
    """
    调用 LLM，返回 async iterator of text chunks（SSE 流式）
    """
    import litellm

    configs = await _get_configs(config_key, db, user_id=user_id)
    if not configs:
        raise ValueError(f"未找到 AI 配置：{config_key}，请在设置中配置对应模型")

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    for idx, config in enumerate(configs):
        kwargs = _build_completion_kwargs(config=config, messages=full_messages, stream=True)
        emitted_content = False
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                content = _extract_delta_content(chunk)
                if content:
                    emitted_content = True
                    yield content
            return
        except Exception as exc:
            if not _is_fallbackable_error(exc):
                raise
            has_next = idx < len(configs) - 1
            if not has_next:
                raise AllModelsExhaustedError(config_key, configs, exc) from exc
            next_config = configs[idx + 1]
            event = build_fallback_event(
                key=config_key,
                from_model=config.model,
                to_model=next_config.model,
                reason=str(exc),
                reset_content=emitted_content,
            )
            await _dispatch_fallback_event(event, on_fallback)
            yield event


async def call_llm_structured(
    messages: list[dict],
    config_key: str,
    response_model: Type[T],
    db: AsyncSession,
    user_id: int,
    system_prompt: Optional[str] = None,
    on_fallback: Optional[FallbackCallback] = None,
) -> T:
    """
    调用 LLM，返回结构化 Pydantic 对象（用于大纲数据等）
    """
    import litellm

    configs = await _get_configs(config_key, db, user_id=user_id)
    if not configs:
        raise ValueError(f"未找到 AI 配置：{config_key}，请在设置中配置对应模型")

    schema = response_model.model_json_schema()
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)
    # 追加结构化输出指令
    full_messages.append({
        "role": "user",
        "content": f"请严格按照以下 JSON Schema 返回结果：\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n只返回 JSON，不要其他内容。"
    })

    last_error: Optional[Exception] = None
    for idx, config in enumerate(configs):
        kwargs = _build_completion_kwargs(
            config=config,
            messages=full_messages,
            stream=False,
            response_format={"type": "json_object"},
        )
        try:
            response = await litellm.acompletion(**kwargs)
            content = _extract_response_message_content(response)
            data = json.loads(content)
            return response_model.model_validate(data)
        except Exception as exc:
            fallbackable = isinstance(exc, (json.JSONDecodeError, ValidationError)) or _is_fallbackable_error(exc)
            if not fallbackable:
                raise
            last_error = exc
            has_next = idx < len(configs) - 1
            if not has_next:
                raise AllModelsExhaustedError(config_key, configs, exc) from exc
            next_config = configs[idx + 1]
            event = build_fallback_event(
                key=config_key,
                from_model=config.model,
                to_model=next_config.model,
                reason=str(exc),
                reset_content=False,
            )
            await _dispatch_fallback_event(event, on_fallback)

    if last_error:
        raise AllModelsExhaustedError(config_key, configs, last_error) from last_error
    raise ValueError(f"未找到可用模型：{config_key}")

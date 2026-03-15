import asyncio
import math
import re
from collections import defaultdict, deque
from time import monotonic
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from pydantic import BaseModel, Field, field_validator
from typing import Any, List, Optional, Literal, TypedDict
from datetime import datetime, timezone

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.setting import AIConfig, AIModelMap, ProviderBaseURLMap
from app.services.llm import build_model_string, normalize_api_key, apply_provider_kwargs

router = APIRouter(prefix="/settings", tags=["settings"])

ConfigType = Literal["text", "image", "video"]
ConfigTypeOrAuto = Literal["text", "image", "video", "auto"]
Manufacturer = Literal[
    "openai",
    "anthropic",
    "deepseek",
    "gemini",
    "xai",
    "qwen",
    "neuxnet",
    "zhipu",
    "volcengine",
    "other",
]
ManufacturerOrAuto = Literal[
    "openai",
    "anthropic",
    "deepseek",
    "gemini",
    "xai",
    "qwen",
    "neuxnet",
    "zhipu",
    "volcengine",
    "other",
    "auto",
]

# key 对应期望配置类型，避免误映射
MODEL_MAP_TYPE: dict[str, ConfigType] = {
    "outlineScriptAgent": "text",
    "storyboardAgent": "text",
    "generateScript": "text",
    "assetsPrompt": "text",
    "assetsImage": "image",
    "videoPrompt": "text",
    "novel_parser": "text",
    "novel_evaluator": "text",
}

TEST_RATE_LIMIT_WINDOW_SECONDS = 60
TEST_RATE_LIMIT_MAX_REQUESTS = 10
_test_request_windows: dict[int, deque[float]] = defaultdict(deque)


def _normalize_fallback_ids(raw_ids: Any, *, strict: bool = False) -> list[int]:
    """
    strict=True: 写入路径（API 入参），非法值直接报错。
    strict=False: 读取路径（历史脏数据兜底），非法值静默跳过。
    """
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list):
        if strict:
            raise ValueError("fallback_config_ids 必须是数组")
        return []

    seen: set[int] = set()
    ordered_ids: list[int] = []
    for raw in raw_ids:
        if not isinstance(raw, int) or raw <= 0:
            if strict:
                raise ValueError("fallback_config_ids 必须是正整数")
            continue
        if raw in seen:
            continue
        ordered_ids.append(raw)
        seen.add(raw)
    return ordered_ids


def _enforce_test_rate_limit(user_id: int) -> None:
    now = monotonic()
    queue = _test_request_windows[user_id]
    while queue and (now - queue[0]) > TEST_RATE_LIMIT_WINDOW_SECONDS:
        queue.popleft()
    if len(queue) >= TEST_RATE_LIMIT_MAX_REQUESTS:
        retry_after = max(1, int(TEST_RATE_LIMIT_WINDOW_SECONDS - (now - queue[0])))
        raise HTTPException(
            status_code=429,
            detail=f"测试请求过于频繁，请在 {retry_after} 秒后重试",
        )
    queue.append(now)

# 使用 16x16 的内置 PNG，避免部分视觉模型拒绝 1x1 输入。
VISION_TEST_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAEElEQVR42mNgGAWjYBTAAAADEAAB1y2EYwAAAABJRU5ErkJggg=="
)

VISION_REFUSAL_PATTERNS = [
    "无法查看图片",
    "无法分析图片",
    "无法读取图片",
    "无法识别图片",
    "看不到图片",
    "不能查看图片",
    "无法查看或分析图片",
    "cannot view image",
    "can't view image",
    "unable to view image",
    "cannot analyze image",
    "can't analyze image",
    "cannot access image",
    "as a text-based model",
]


# ========== AI Config ===========

class AIConfigCreate(BaseModel):
    type: Optional[ConfigTypeOrAuto] = None
    manufacturer: Optional[ManufacturerOrAuto] = None
    model: str
    api_key: str
    base_url: Optional[str] = None

    @field_validator("model")
    @classmethod
    def validate_required_model(cls, value: str):
        val = value.strip()
        if not val:
            raise ValueError("字段不能为空")
        return val

    @field_validator("api_key")
    @classmethod
    def normalize_required_api_key(cls, value: str):
        key = normalize_api_key(value)
        if not key:
            raise ValueError("字段不能为空")
        return key

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: Optional[str]):
        if value is None:
            return None
        val = value.strip()
        return val or None


class AIConfigUpdate(BaseModel):
    type: Optional[ConfigType] = None
    manufacturer: Optional[Manufacturer] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    @field_validator("model")
    @classmethod
    def validate_optional_model(cls, value: Optional[str]):
        if value is None:
            return None
        val = value.strip()
        if not val:
            raise ValueError("字段不能为空")
        return val

    @field_validator("api_key")
    @classmethod
    def normalize_optional_api_key(cls, value: Optional[str]):
        if value is None:
            return None
        key = normalize_api_key(value)
        if not key:
            raise ValueError("字段不能为空")
        return key

    @field_validator("base_url")
    @classmethod
    def normalize_optional_base_url(cls, value: Optional[str]):
        if value is None:
            return None
        val = value.strip()
        return val or None


class AIConfigTestReq(BaseModel):
    type: ConfigTypeOrAuto = "auto"
    manufacturer: Optional[ManufacturerOrAuto] = None
    model: str
    api_key: str
    base_url: Optional[str] = None
    prompt: str = Field(default="Reply with 'OK'.")

    @field_validator("model")
    @classmethod
    def validate_test_model(cls, value: str):
        val = value.strip()
        if not val:
            raise ValueError("字段不能为空")
        return val

    @field_validator("api_key")
    @classmethod
    def normalize_test_api_key(cls, value: str):
        key = normalize_api_key(value)
        if not key:
            raise ValueError("字段不能为空")
        return key


class AIConfigOut(BaseModel):
    id: int
    type: str
    manufacturer: str
    model: str
    api_key: str
    base_url: Optional[str]
    last_test_status: Optional[str]
    last_test_summary: Optional[str]
    last_tested_at: Optional[datetime]
    supports_tools: Optional[bool]
    supports_thinking: Optional[bool]
    supports_vision: Optional[bool]
    supports_image_generation: Optional[bool]
    image_min_size: Optional[str]
    supports_video_generation: Optional[bool]
    created_at: datetime
    model_config = {"from_attributes": True}


class CapabilityResult(TypedDict):
    reply: str
    detected_type: Optional[str]
    supports_tools: Optional[bool]
    supports_thinking: Optional[bool]
    supports_vision: Optional[bool]
    supports_image_generation: Optional[bool]
    image_min_size: Optional[str]
    supports_video_generation: Optional[bool]


class ProviderBaseURLMapCreate(BaseModel):
    manufacturer: Manufacturer
    base_url_prefix: str

    @field_validator("base_url_prefix")
    @classmethod
    def validate_base_url_prefix(cls, value: str):
        normalized = _normalize_base_url_for_match(value)
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class ProviderBaseURLMapUpdate(BaseModel):
    manufacturer: Optional[Manufacturer] = None
    base_url_prefix: Optional[str] = None

    @field_validator("base_url_prefix")
    @classmethod
    def validate_optional_base_url_prefix(cls, value: Optional[str]):
        if value is None:
            return None
        normalized = _normalize_base_url_for_match(value)
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class ProviderBaseURLMapOut(BaseModel):
    id: int
    manufacturer: str
    base_url_prefix: str
    created_at: datetime
    model_config = {"from_attributes": True}


def _infer_manufacturer(*, manufacturer: Optional[str], base_url: Optional[str], model: str) -> Manufacturer:
    model_name = (model or "").strip().lower()
    provided = (manufacturer or "").strip().lower()
    host = _extract_host(base_url)
    hinted = _infer_manufacturer_by_model(model_name)

    if host:
        host_specific = _infer_manufacturer_by_host(host, hinted=hinted)
        if host_specific:
            return host_specific

    if provided and provided != "auto":
        return provided  # type: ignore[return-value]
    if hinted:
        return hinted
    if host:
        return "other"
    return "other"


def _normalize_base_url_for_match(base_url: Optional[str]) -> Optional[str]:
    raw = (base_url or "").strip().lower()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    return candidate.rstrip("/")


async def _resolve_manufacturer(
    *,
    db: AsyncSession,
    user_id: int,
    manufacturer: Optional[str],
    base_url: Optional[str],
    model: str,
) -> Manufacturer:
    normalized_target = _normalize_base_url_for_match(base_url)
    if normalized_target:
        result = await db.execute(
            select(ProviderBaseURLMap).where(ProviderBaseURLMap.user_id == user_id)
        )
        mappings = result.scalars().all()
        best_match: Optional[ProviderBaseURLMap] = None
        best_len = -1
        for mapping in mappings:
            prefix = _normalize_base_url_for_match(mapping.base_url_prefix)
            if not prefix:
                continue
            if normalized_target.startswith(prefix) and len(prefix) > best_len:
                best_match = mapping
                best_len = len(prefix)
        if best_match:
            return best_match.manufacturer  # type: ignore[return-value]

    return _infer_manufacturer(
        manufacturer=manufacturer,
        base_url=base_url,
        model=model,
    )


def _extract_host(base_url: Optional[str]) -> Optional[str]:
    raw = (base_url or "").strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return None
    host = (parsed.hostname or "").lower().strip()
    return host or None


def _infer_manufacturer_by_host(host: str, *, hinted: Optional[Manufacturer]) -> Optional[Manufacturer]:
    if host == "api.openai.com":
        return "openai"
    if host == "api.deepseek.com":
        return "deepseek"
    if host == "api.anthropic.com":
        return "anthropic"
    if host == "api.x.ai":
        return "xai"
    if host.endswith("neuxnet.com"):
        return "neuxnet"
    if host == "open.bigmodel.cn":
        return "zhipu"
    if host.endswith("volces.com") or host.endswith("volcengineapi.com"):
        return "volcengine"
    if host.endswith("generativelanguage.googleapis.com") or host.endswith("ai.google.dev"):
        return "gemini"

    # 阿里云域名下模型种类较多，优先用模型特征而不是固定 endpoint -> provider 写死映射。
    if host.endswith("aliyuncs.com"):
        if hinted:
            return hinted
        return "other"
    return None


def _infer_manufacturer_by_model(model_name: str) -> Optional[Manufacturer]:
    if not model_name:
        return None
    candidates = [model_name]
    if "/" in model_name:
        candidates.append(model_name.split("/", 1)[1])

    for item in candidates:
        if item.startswith(("qwen", "qwq", "wanx", "tongyi")):
            return "qwen"
        if item.startswith("neuxnet/"):
            return "neuxnet"
        if item.startswith(("glm", "charglm", "cogview", "cogvideox")):
            return "zhipu"
        if item.startswith(("doubao", "seed")):
            return "volcengine"
        if item.startswith("deepseek"):
            return "deepseek"
        if item.startswith(("claude", "anthropic/")):
            return "anthropic"
        if item.startswith("gemini"):
            return "gemini"
        if item.startswith(("grok", "xai/")):
            return "xai"
        if item.startswith(("gpt", "o1", "o3", "o4", "text-embedding", "dall-e", "openai/")):
            return "openai"
    return None


def _infer_probe_order(raw_model: str, *, base_url: Optional[str] = None) -> list[ConfigType]:
    model_lower = raw_model.lower()
    base_url_lower = (base_url or "").lower()
    if any(k in base_url_lower for k in ["/images/", "/images/generations", "/image/"]):
        return ["image", "text", "video"]
    if any(k in base_url_lower for k in ["/videos/", "/videos/generations", "/video/"]):
        return ["video", "image", "text"]

    video_keywords = ["video", "t2v", "wanx2", "kling", "veo", "hunyuan-video", "seedance"]
    image_keywords = ["image", "wanx", "flux", "stable-diffusion", "sdxl", "dall", "edit", "seedream"]
    vision_keywords = ["vl", "vision", "multimodal"]

    if any(k in model_lower for k in vision_keywords):
        return ["text", "image", "video"]
    if any(k in model_lower for k in video_keywords):
        return ["video", "image", "text"]
    if any(k in model_lower for k in image_keywords):
        return ["image", "text", "video"]
    return ["text", "image", "video"]


def _infer_auto_probe_budget(raw_model: str, *, base_url: Optional[str] = None) -> tuple[int, str]:
    model_lower = (raw_model or "").lower()
    base_url_lower = (base_url or "").lower()

    if any(k in base_url_lower for k in ["/images/", "/images/generations", "/image/"]):
        return 1, "endpoint-image"
    if any(k in base_url_lower for k in ["/videos/", "/videos/generations", "/video/"]):
        return 1, "endpoint-video"

    if any(k in model_lower for k in ["seedream", "wanx", "flux", "sdxl", "dall", "image", "edit"]):
        return 1, "model-image"
    if any(k in model_lower for k in ["seedance", "wanx2", "video", "t2v", "kling", "veo"]):
        return 1, "model-video"
    if any(k in model_lower for k in ["vl", "vision", "multimodal"]):
        return 2, "model-vision"

    # 无明显信号时，保留完整探测，确保兼容未知模型。
    return 3, "fallback"


def _can_continue_probe(error_text: str, probe: ConfigType) -> bool:
    low = error_text.lower()
    hard_patterns = [
        "authenticationerror",
        "incorrect api key",
        "invalid api key",
        "unauthorized",
        "forbidden",
        "rate limit",
        "quota",
        "timeout",
        "timed out",
        "connection",
        "dns",
        "ssl",
        "certificate",
    ]
    if any(p in low for p in hard_patterns):
        return False

    soft_patterns_common = [
        "404",
        "notfounderror",
        "notfound",
        "not found",
        "unsupported",
        "does not support",
        "invalid request",
        "badrequest",
        "specified action is invalid",
    ]
    if any(p in low for p in soft_patterns_common):
        return True

    if probe == "text" and any(p in low for p in ["messages", "chat completion", "tool"]):
        return True
    if probe == "image" and any(p in low for p in ["image", "images/"]):
        return True
    if probe == "video" and any(p in low for p in ["video"]):
        return True

    return False


@router.get("/ai-configs", response_model=List[AIConfigOut])
async def list_ai_configs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIConfig).where(AIConfig.user_id == user.id))
    return result.scalars().all()


@router.post("/ai-configs", response_model=AIConfigOut)
async def create_ai_config(
    body: AIConfigCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved_manufacturer = await _resolve_manufacturer(
        db=db,
        user_id=user.id,
        manufacturer=body.manufacturer,
        base_url=body.base_url,
        model=body.model,
    )
    requested_type = body.type if body.type in {"text", "image", "video"} else "auto"
    initial_type = requested_type if requested_type != "auto" else "text"

    config = AIConfig(
        type=initial_type,
        manufacturer=resolved_manufacturer,
        model=body.model,
        api_key=body.api_key,
        base_url=body.base_url,
        user_id=user.id,
    )
    db.add(config)
    await db.flush()

    try:
        result = await _test_llm_connectivity(
            config_type=requested_type,
            manufacturer=resolved_manufacturer,
            model=body.model,
            api_key=body.api_key,
            base_url=body.base_url,
            prompt="Reply with 'OK'.",
        )
        detected_type = result.get("detected_type")
        if detected_type in {"text", "image", "video"}:
            config.type = detected_type
        _set_config_test_result(config, status="passed", summary=result["reply"], result=result)
    except Exception as e:
        if requested_type == "auto":
            failure_result: CapabilityResult = {
                "reply": str(e),
                "detected_type": config.type,
                "supports_tools": None,
                "supports_thinking": None,
                "supports_vision": None,
                "supports_image_generation": None,
                "image_min_size": None,
                "supports_video_generation": None,
            }
        else:
            failure_result = {
                "reply": str(e),
                "detected_type": config.type,
                "supports_tools": False if config.type == "text" else None,
                "supports_thinking": None,
                "supports_vision": False if config.type == "text" else None,
                "supports_image_generation": False if config.type == "image" else None,
                "image_min_size": None,
                "supports_video_generation": False if config.type == "video" else None,
            }
        _set_config_test_result(config, status="failed", summary=str(e), result=failure_result)

    await db.flush()
    await db.refresh(config)
    return config


@router.put("/ai-configs/{config_id}", response_model=AIConfigOut)
async def update_ai_config(
    config_id: int,
    body: AIConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id, AIConfig.user_id == user.id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="未提供需要更新的字段")

    for k, v in payload.items():
        setattr(config, k, v)
    if "manufacturer" not in payload and ("model" in payload or "base_url" in payload):
        config.manufacturer = await _resolve_manufacturer(
            db=db,
            user_id=user.id,
            manufacturer=None,
            base_url=config.base_url,
            model=config.model,
        )
    await db.flush()
    await db.refresh(config)
    return config


@router.delete("/ai-configs/{config_id}")
async def delete_ai_config(
    config_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id, AIConfig.user_id == user.id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    # 被 model map 引用时禁止删除（主模型或 fallback 链）
    map_result = await db.execute(
        select(AIModelMap).where(
            or_(
                AIModelMap.config_id == config_id,
                AIModelMap.fallback_config_ids.contains([config_id]),
            )
        )
    )
    mapped = map_result.scalars().first()
    if mapped:
        if mapped.config_id == config_id:
            raise HTTPException(status_code=409, detail=f"该配置正在被映射「{mapped.key}」使用，无法删除")
        raise HTTPException(status_code=409, detail=f"该配置正在被映射「{mapped.key}」的 fallback 链使用，无法删除")

    await db.delete(config)
    return {"code": 0}




async def _test_llm_connectivity(
    *,
    config_type: str,
    manufacturer: str,
    model: str,
    api_key: str,
    base_url: Optional[str],
    prompt: str,
) -> CapabilityResult:
    model_name = build_model_string(manufacturer, model)
    kwargs_base: dict[str, Any] = {"model": model_name, "api_key": normalize_api_key(api_key)}
    kwargs_base = apply_provider_kwargs(kwargs_base, manufacturer, base_url)

    if config_type == "auto":
        return await _test_auto_connectivity(
            kwargs_base,
            prompt=prompt,
            raw_model=model,
            raw_base_url=base_url,
        )
    if config_type == "image":
        return await _test_image_connectivity(kwargs_base)
    if config_type == "text":
        return await _test_text_connectivity(kwargs_base, prompt=prompt, raw_model=model)
    if config_type == "video":
        return await _test_video_connectivity(kwargs_base)
    raise ValueError(f"不支持的配置类型: {config_type}")


def _truncate_summary(text: str, max_len: int = 2000) -> str:
    cleaned = (text or "").strip()
    return cleaned[:max_len]


def _set_config_test_result(
    config: AIConfig,
    *,
    status: str,
    summary: str,
    result: Optional[CapabilityResult] = None,
) -> None:
    config.last_test_status = status
    config.last_test_summary = _truncate_summary(summary)
    config.last_tested_at = datetime.now(tz=timezone.utc)
    if result is None:
        config.supports_tools = None
        config.supports_thinking = None
        config.supports_vision = None
        config.supports_image_generation = None
        config.image_min_size = None
        config.supports_video_generation = None
        return
    config.supports_tools = result.get("supports_tools")
    config.supports_thinking = result.get("supports_thinking")
    config.supports_vision = result.get("supports_vision")
    config.supports_image_generation = result.get("supports_image_generation")
    config.image_min_size = result.get("image_min_size")
    config.supports_video_generation = result.get("supports_video_generation")


def _read_field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_first_tool_name(choice_message: Any) -> Optional[str]:
    tool_calls = _read_field(choice_message, "tool_calls")
    if not tool_calls:
        return None
    first = tool_calls[0]
    fn = _read_field(first, "function")
    return _read_field(fn, "name")


def _detect_thinking(raw_model: str, choice_message: Any, choice_obj: Any) -> tuple[bool, str]:
    model_lower = raw_model.lower()
    keyword_hits = [
        "reasoner",
        "thinking",
        "qwq",
        "r1",
        "o1",
        "o3",
        "o4",
    ]
    if any(k in model_lower for k in keyword_hits):
        return True, "model-name"

    msg_reasoning = _read_field(choice_message, "reasoning_content") or _read_field(choice_message, "reasoning")
    if msg_reasoning:
        return True, "response-message"

    choice_reasoning = _read_field(choice_obj, "reasoning") or _read_field(choice_obj, "reasoning_content")
    if choice_reasoning:
        return True, "response-choice"

    return False, "none"


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"}:
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
        return " ".join(text_parts).strip()
    return ""


def _extract_min_image_pixels(error_text: str) -> Optional[int]:
    match = re.search(r"at least\s+(\d+)\s+pixels", (error_text or "").lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _size_from_min_pixels(min_pixels: int) -> str:
    side = int(math.isqrt(max(1, min_pixels)))
    if side * side < min_pixels:
        side += 1
    # 多数图像模型偏好 64 对齐分辨率
    if side % 64 != 0:
        side += 64 - (side % 64)
    return f"{side}x{side}"


def _image_probe_sizes() -> list[str]:
    # 统一从小尺寸开始，遇到最小像素限制再自适应升档。
    return ["512x512", "1024x1024", "1536x1536"]


def _is_vision_refusal(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    for pattern in VISION_REFUSAL_PATTERNS:
        if pattern in low:
            return True
    return False


def _volcengine_video_endpoint(base_url: Optional[str]) -> Optional[str]:
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    lower_path = path.lower()
    has_task_path = "/contents/generations/tasks" in lower_path
    host_is_volcengine = bool(host) and (
        host.endswith("volces.com")
        or host.endswith("volcengineapi.com")
        or "volces.com" in host
        or "volcengineapi.com" in host
    )
    # 兼容代理网关：即便 host 不是 volces.com，只要 path 明确是 tasks 接口也按 Volcengine 视频接口处理。
    if not host_is_volcengine and not has_task_path:
        return None
    # 兼容用户把具体视频动作地址填进来，统一收敛到 tasks 根路径。
    if lower_path.endswith("/contents/generations/tasks/videos"):
        path = path[: -len("/videos")]
    elif lower_path.endswith("/videos/generations"):
        path = path[: -len("/videos/generations")] + "/contents/generations/tasks"
    elif has_task_path:
        idx = lower_path.find("/contents/generations/tasks")
        path = path[: idx + len("/contents/generations/tasks")]
    if path.endswith("/contents/generations/tasks"):
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return f"{parsed.scheme}://{parsed.netloc}{path}/contents/generations/tasks"


def _strip_provider_prefix(model_name: str) -> str:
    model = (model_name or "").strip()
    if "/" not in model:
        return model
    return model.split("/", 1)[1]


async def _probe_chat(kwargs_base: dict[str, Any], *, prompt: str) -> tuple[bool, str, str]:
    from litellm import acompletion

    try:
        basic_res = await acompletion(
            **kwargs_base,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            max_tokens=128,
        )
        basic_choice = basic_res.choices[0]
        basic_message = basic_choice.message
        basic_reply = _extract_text_content(_read_field(basic_message, "content"))
        if not basic_reply:
            return False, "Chat 失败：模型无有效回复", ""
        return True, "Chat 通过", basic_reply
    except Exception as e:
        return False, f"Chat 异常：{str(e)}", ""


async def _probe_tools(kwargs_base: dict[str, Any]) -> tuple[bool, str]:
    from litellm import acompletion

    try:
        tool_res = await acompletion(
            **kwargs_base,
            messages=[
                {
                    "role": "user",
                    "content": "请务必调用工具 ping_tool，参数 text 固定为 pong。不要直接文本回答。",
                }
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "ping_tool",
                        "description": "Connectivity test helper tool",
                        "parameters": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                }
            ],
            tool_choice="auto",
            stream=False,
            max_tokens=128,
        )
        tool_choice = tool_res.choices[0]
        tool_message = tool_choice.message
        tool_name = _extract_first_tool_name(tool_message)
        if not tool_name:
            return False, "Tools 未触发"
        if tool_name != "ping_tool":
            return False, f"Tools 触发了非预期工具({tool_name})"
        return True, "Tools 支持"
    except Exception as e:
        return False, f"Tools 异常：{str(e)}"


async def _probe_thinking(kwargs_base: dict[str, Any], *, raw_model: str) -> tuple[Optional[bool], str]:
    from litellm import acompletion

    try:
        thinking_res = await acompletion(
            **kwargs_base,
            messages=[{"role": "user", "content": "请回答 2+3=?，只返回答案。"}],
            stream=False,
            max_tokens=32,
        )
        thinking_choice = thinking_res.choices[0]
        thinking_message = thinking_choice.message
        is_thinking, thinking_source = _detect_thinking(raw_model, thinking_message, thinking_choice)
        return is_thinking, f"Thinking {'是' if is_thinking else '否'}（来源: {thinking_source}）"
    except Exception as e:
        return None, f"Thinking 探测异常：{str(e)}"


async def _probe_vision(kwargs_base: dict[str, Any]) -> tuple[bool, str]:
    from litellm import acompletion

    try:
        vision_res = await acompletion(
            **kwargs_base,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请用一句话描述图片内容。"},
                        {"type": "image_url", "image_url": {"url": VISION_TEST_IMAGE_DATA_URL}},
                    ],
                }
            ],
            stream=False,
            max_tokens=64,
        )
        vision_choice = vision_res.choices[0]
        vision_message = vision_choice.message
        vision_reply = _extract_text_content(_read_field(vision_message, "content"))
        if not vision_reply:
            return False, "Vision 失败：图片输入后无文本输出"
        if _is_vision_refusal(vision_reply):
            return False, f"Vision 失败：模型返回拒绝查看图片（{vision_reply[:120]}）"
        return True, f"Vision 支持；样例回复: {vision_reply[:120]}"
    except Exception as e:
        return False, f"Vision 异常：{str(e)}"


async def _test_text_connectivity(kwargs_base: dict[str, Any], *, prompt: str, raw_model: str) -> CapabilityResult:
    (chat_ok, chat_msg, basic_reply), (supports_tools, tools_msg), (supports_thinking, thinking_msg), (supports_vision, vision_msg) = await asyncio.gather(
        _probe_chat(kwargs_base, prompt=prompt),
        _probe_tools(kwargs_base),
        _probe_thinking(kwargs_base, raw_model=raw_model),
        _probe_vision(kwargs_base),
    )
    if not chat_ok:
        raise ValueError(chat_msg)

    thinking_label = "未知" if supports_thinking is None else ("是" if supports_thinking else "否")
    vision_label = "是" if supports_vision else "否"
    return {
        "reply": (
            f"{chat_msg}；{tools_msg}；Thinking {thinking_label}；{thinking_msg}；"
            f"Vision {vision_label}；{vision_msg}；样例回复: {basic_reply[:120]}"
        ),
        "detected_type": "text",
        "supports_tools": supports_tools,
        "supports_thinking": supports_thinking,
        "supports_vision": supports_vision,
        "supports_image_generation": None,
        "image_min_size": None,
        "supports_video_generation": None,
    }


async def _test_image_connectivity(kwargs_base: dict[str, Any]) -> CapabilityResult:
    from litellm import aimage_generation

    pending_sizes = _image_probe_sizes()
    seen_sizes: set[str] = set()
    last_error = "图片能力探测失败"
    min_size: Optional[str] = None

    while pending_sizes:
        size = pending_sizes.pop(0)
        if size in seen_sizes:
            continue
        seen_sizes.add(size)
        try:
            image_res = await aimage_generation(
                **kwargs_base,
                prompt="Generate a simple black-and-white checkmark icon, minimal style.",
                size=size,
            )
            items = _read_field(image_res, "data") or []
            if not items:
                raise ValueError("图片模型返回为空")
            first = items[0]
            image_url = _read_field(first, "url")
            b64_data = _read_field(first, "b64_json")
            min_suffix = f"；最小尺寸约束: {min_size}" if min_size else ""
            if image_url:
                return {
                    "reply": f"图片生成成功(size={size}){min_suffix}；返回 URL: {str(image_url)[:180]}",
                    "detected_type": "image",
                    "supports_tools": None,
                    "supports_thinking": None,
                    "supports_vision": None,
                    "supports_image_generation": True,
                    "image_min_size": min_size,
                    "supports_video_generation": None,
                }
            if b64_data:
                return {
                    "reply": f"图片生成成功(size={size}){min_suffix}；返回 b64 数据，长度: {len(str(b64_data))}",
                    "detected_type": "image",
                    "supports_tools": None,
                    "supports_thinking": None,
                    "supports_vision": None,
                    "supports_image_generation": True,
                    "image_min_size": min_size,
                    "supports_video_generation": None,
                }
            raise ValueError("图片模型返回中未包含 url 或 b64_json")
        except Exception as e:
            msg = str(e)
            last_error = msg
            min_pixels = _extract_min_image_pixels(msg)
            if min_pixels:
                adaptive_size = _size_from_min_pixels(min_pixels)
                min_size = adaptive_size
                if adaptive_size not in seen_sizes and adaptive_size not in pending_sizes:
                    pending_sizes.insert(0, adaptive_size)
                continue
            if "size" in msg.lower() and "not valid" in msg.lower():
                continue
            raise

    raise ValueError(last_error)


async def _test_video_connectivity(kwargs_base: dict[str, Any]) -> CapabilityResult:
    # Volcengine 视频模型优先走官方任务 API，避免 openai-compatible 路径拼接成 /videos 导致 InvalidAction。
    endpoint = _volcengine_video_endpoint(str(kwargs_base.get("base_url") or kwargs_base.get("api_base") or ""))
    api_key = str(kwargs_base.get("api_key") or "").strip()
    if endpoint:
        if not api_key:
            raise ValueError("Volcengine 视频任务提交失败: API Key 为空")
        model_name = _strip_provider_prefix(str(kwargs_base.get("model") or ""))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        text_payload = {
            "model": model_name,
            "content": [
                {
                    "type": "text",
                    # 仅做连通性最小探测，避免注入模型未支持的时长/水印参数。
                    "text": "A calm mountain lake at sunrise, cinematic short video.",
                }
            ],
        }
        image_payload = {
            "model": model_name,
            "content": [
                {
                    "type": "text",
                    # 图生视频也使用同一最小文本指令，降低参数不兼容概率。
                    "text": "A calm mountain lake at sunrise, cinematic short video.",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_i2v.png",
                    },
                },
            ],
        }
        payloads = [text_payload, image_payload]
        last_error = ""
        async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
            for idx, payload in enumerate(payloads):
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code < 400:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    task_id = (
                        _read_field(body, "id")
                        or _read_field(body, "task_id")
                        or _read_field(_read_field(body, "data"), "id")
                        or "N/A"
                    )
                    return {
                        "reply": f"视频提交通过(volcengine-task)；task_id={task_id}；endpoint={endpoint}",
                        "detected_type": "video",
                        "supports_tools": None,
                        "supports_thinking": None,
                        "supports_vision": None,
                        "supports_image_generation": None,
                        "image_min_size": None,
                        "supports_video_generation": True,
                    }
                err_text = resp.text.strip()
                last_error = f"HTTP {resp.status_code}: {err_text[:260]}"
                low = err_text.lower()
                need_image = any(k in low for k in ["image_url", "image", "reference image", "i2v"])
                if idx == 0 and need_image:
                    continue
                break
        raise ValueError(f"Volcengine 视频任务提交失败: {last_error}")

    from litellm import avideo_generation, avideo_status

    # 最小粒度能力探测：验证“可提交视频生成任务”。
    create_res = await avideo_generation(
        **kwargs_base,
        prompt="Generate a 1-second static black frame video.",
    )
    video_id = _read_field(create_res, "id") or _read_field(create_res, "video_id")
    status = _read_field(create_res, "status") or _read_field(create_res, "state")
    videos = _read_field(create_res, "videos") or []
    has_inline_video = bool(videos)
    if not any([video_id, status, has_inline_video]):
        raise ValueError("视频任务提交失败：未返回任务标识或状态")

    status_probe = "状态探测未执行"
    if video_id:
        try:
            status_res = await asyncio.wait_for(
                avideo_status(video_id=video_id, **kwargs_base),
                timeout=8,
            )
            probed_status = _read_field(status_res, "status") or _read_field(status_res, "state") or "unknown"
            status_probe = f"状态探测: {probed_status}"
        except Exception as e:
            status_probe = f"状态探测略过: {str(e)[:120]}"

    return {
        "reply": (
            f"视频提交通过；video_id={video_id or 'N/A'}；create_status={status or 'N/A'}；"
            f"{status_probe}"
        ),
        "detected_type": "video",
        "supports_tools": None,
        "supports_thinking": None,
        "supports_vision": None,
        "supports_image_generation": None,
        "image_min_size": None,
        "supports_video_generation": True,
    }


async def _test_auto_connectivity(
    kwargs_base: dict[str, Any],
    *,
    prompt: str,
    raw_model: str,
    raw_base_url: Optional[str] = None,
) -> CapabilityResult:
    order = _infer_probe_order(raw_model, base_url=raw_base_url)
    max_probes, reason = _infer_auto_probe_budget(raw_model, base_url=raw_base_url)
    trace: list[str] = []
    attempted = 0

    for probe in order:
        if attempted >= max_probes:
            break
        attempted += 1
        try:
            if probe == "text":
                result = await _test_text_connectivity(kwargs_base, prompt=prompt, raw_model=raw_model)
            elif probe == "image":
                result = await _test_image_connectivity(kwargs_base)
            else:
                result = await _test_video_connectivity(kwargs_base)
            result["reply"] = f"自动识别类型: {probe}；{result['reply']}"
            result["detected_type"] = probe
            return result
        except Exception as e:
            msg = str(e)
            trace.append(f"{probe}: {msg}")
            if not _can_continue_probe(msg, probe):
                raise ValueError(f"自动探测在 {probe} 探针失败（硬错误）: {msg}")

    raise ValueError(
        f"自动探测失败：未找到可用能力。策略={reason}，已尝试{attempted}个探针。"
        f"探针轨迹：{' | '.join(trace)}"
    )


@router.post("/ai-configs/test")
async def test_ai_config(
    body: AIConfigTestReq,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _enforce_test_rate_limit(user.id)
    resolved_manufacturer = await _resolve_manufacturer(
        db=db,
        user_id=user.id,
        manufacturer=body.manufacturer,
        base_url=body.base_url,
        model=body.model,
    )
    try:
        result = await _test_llm_connectivity(
            config_type=body.type,
            manufacturer=resolved_manufacturer,
            model=body.model,
            api_key=body.api_key,
            base_url=body.base_url,
            prompt=body.prompt,
        )
        return {
            "code": 0,
            "data": {
                "reply": _truncate_summary(result["reply"], 500),
                "detected_type": result.get("detected_type"),
                "supports_tools": result.get("supports_tools"),
                "supports_thinking": result.get("supports_thinking"),
                "supports_vision": result.get("supports_vision"),
                "supports_image_generation": result.get("supports_image_generation"),
                "image_min_size": result.get("image_min_size"),
                "supports_video_generation": result.get("supports_video_generation"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模型能力测试失败: {str(e)}")


@router.post("/ai-configs/{config_id}/test")
async def test_saved_ai_config(
    config_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _enforce_test_rate_limit(user.id)
    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id, AIConfig.user_id == user.id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    try:
        resolved_manufacturer = await _resolve_manufacturer(
            db=db,
            user_id=user.id,
            manufacturer=config.manufacturer,
            base_url=config.base_url,
            model=config.model,
        )
        config.manufacturer = resolved_manufacturer
        config_type = config.type if config.type in {"text", "image", "video"} else "auto"
        result = await _test_llm_connectivity(
            config_type=config_type,
            manufacturer=resolved_manufacturer,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            prompt="Reply with 'OK'.",
        )
        detected_type = result.get("detected_type")
        if config_type == "auto" and detected_type in {"text", "image", "video"}:
            config.type = detected_type
        _set_config_test_result(config, status="passed", summary=result["reply"], result=result)
        await db.flush()
        await db.refresh(config)
        return {
            "code": 0,
            "data": {
                "reply": _truncate_summary(result["reply"], 500),
                "detected_type": result.get("detected_type", config.type),
                "supports_tools": result.get("supports_tools"),
                "supports_thinking": result.get("supports_thinking"),
                "supports_vision": result.get("supports_vision"),
                "supports_image_generation": result.get("supports_image_generation"),
                "image_min_size": result.get("image_min_size"),
                "supports_video_generation": result.get("supports_video_generation"),
            },
        }
    except Exception as e:
        failure_result: CapabilityResult = {
            "reply": str(e),
            "detected_type": config.type,
            "supports_tools": False if config.type == "text" else None,
            "supports_thinking": None,
            "supports_vision": False if config.type == "text" else None,
            "supports_image_generation": False if config.type == "image" else None,
            "image_min_size": None,
            "supports_video_generation": False if config.type == "video" else None,
        }
        _set_config_test_result(config, status="failed", summary=str(e), result=failure_result)
        await db.flush()
        raise HTTPException(status_code=400, detail=f"模型能力测试失败: {str(e)}")

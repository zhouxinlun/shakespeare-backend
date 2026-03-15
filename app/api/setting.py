from fastapi import APIRouter

from app.api import setting_config, setting_map, setting_prompt
from app.api.setting_config import (
    _can_continue_probe,
    _infer_auto_probe_budget,
    _infer_manufacturer,
    _infer_probe_order,
    _probe_vision,
    _resolve_manufacturer,
    _test_auto_connectivity,
    _test_image_connectivity,
    _test_video_connectivity,
    _volcengine_video_endpoint,
)

router = APIRouter()
router.include_router(setting_config.router)
router.include_router(setting_map.router)
router.include_router(setting_prompt.router)

__all__ = [
    "router",
    "_test_image_connectivity",
    "_test_video_connectivity",
    "_probe_vision",
    "_test_auto_connectivity",
    "_infer_manufacturer",
    "_infer_probe_order",
    "_infer_auto_probe_budget",
    "_volcengine_video_endpoint",
    "_resolve_manufacturer",
    "_can_continue_probe",
]

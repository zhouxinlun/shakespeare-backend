"""
Shakespeare Backend - FastAPI 主入口
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.config import settings
from app.database import engine
from app.api import auth, project, novel, outline, pipeline, setting


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据库结构由 Alembic 管理；启动时仅做默认数据初始化
    await init_default_data()
    yield
    await engine.dispose()


async def init_default_data():
    """初始化默认数据"""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.user import User
    from app.models.setting import AIModelMap, Prompt
    from app.core.security import hash_password
    from app.prompts.outline import STORYLINE_AGENT_SYSTEM, OUTLINE_AGENT_SYSTEM, DIRECTOR_AGENT_SYSTEM
    from app.prompts.script import SCRIPT_AGENT_SYSTEM, SCRIPT_CHAT_SYSTEM
    from app.prompts.storyboard import SEGMENT_AGENT_SYSTEM, SHOT_AGENT_SYSTEM, STORYBOARD_CHAT_SYSTEM

    async with AsyncSessionLocal() as db:
        try:
            # 默认 admin 用户
            result = await db.execute(select(User).where(User.name == "admin"))
            if not result.scalar_one_or_none():
                db.add(User(name="admin", password=hash_password("admin123")))

            # AI Model Map（agent key → config 映射）
            default_maps = [
                {"key": "outlineScriptAgent", "name": "大纲故事线 Agent"},
                {"key": "storyboardAgent", "name": "分镜 Agent"},
                {"key": "generateScript", "name": "剧本生成"},
                {"key": "assetsPrompt", "name": "资产提示词润色"},
                {"key": "assetsImage", "name": "资产图片生成"},
                {"key": "videoPrompt", "name": "视频提示词生成"},
                {"key": "novel_parser", "name": "小说解析"},
                {"key": "novel_evaluator", "name": "文本评估"},
            ]
            for m in default_maps:
                result = await db.execute(select(AIModelMap).where(AIModelMap.key == m["key"]))
                if not result.scalar_one_or_none():
                    db.add(AIModelMap(key=m["key"], name=m["name"]))

            # 默认 Prompts
            default_prompts = [
                {"code": "outlineScript-a1", "name": "故事师 AI1", "type": "subAgent", "parent_code": "outlineScript-main", "default_value": STORYLINE_AGENT_SYSTEM},
                {"code": "outlineScript-a2", "name": "大纲师 AI2", "type": "subAgent", "parent_code": "outlineScript-main", "default_value": OUTLINE_AGENT_SYSTEM},
                {"code": "outlineScript-director", "name": "导演 Director", "type": "subAgent", "parent_code": "outlineScript-main", "default_value": DIRECTOR_AGENT_SYSTEM},
                {"code": "script-main", "name": "剧本生成", "type": "mainAgent", "parent_code": None, "default_value": SCRIPT_AGENT_SYSTEM},
                {"code": "script-chat", "name": "剧本 Chat 优化", "type": "subAgent", "parent_code": "script-main", "default_value": SCRIPT_CHAT_SYSTEM},
                {"code": "storyboard-segment", "name": "分镜片段拆分", "type": "subAgent", "parent_code": "storyboard-main", "default_value": SEGMENT_AGENT_SYSTEM},
                {"code": "storyboard-shot", "name": "分镜生成", "type": "subAgent", "parent_code": "storyboard-main", "default_value": SHOT_AGENT_SYSTEM},
                {"code": "storyboard-chat", "name": "分镜 Chat 优化", "type": "subAgent", "parent_code": "storyboard-main", "default_value": STORYBOARD_CHAT_SYSTEM},
            ]
            for p in default_prompts:
                result = await db.execute(select(Prompt).where(Prompt.code == p["code"]))
                if not result.scalar_one_or_none():
                    db.add(Prompt(**p))

            await db.commit()
        except (ProgrammingError, OperationalError) as exc:
            await db.rollback()
            raise RuntimeError("Database schema is not initialized. Please run `alembic upgrade head`.") from exc


app = FastAPI(
    title="Shakespeare API",
    description="AI 短剧自动生成平台 - 状态机驱动的创作流水线",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（上传的图片/视频）
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# 注册路由
app.include_router(auth.router, prefix="/api")
app.include_router(project.router, prefix="/api")
app.include_router(novel.router, prefix="/api")
app.include_router(outline.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(setting.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "shakespeare-backend"}

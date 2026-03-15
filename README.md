# Shakespeare Backend

AI 短剧生成平台后端服务 —— 基于状态机驱动的创作流水线。

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.12 + FastAPI | Web 框架 |
| LangGraph | Agent 状态机编排 |
| liteLLM | 多 LLM Provider 统一接口 |
| SQLAlchemy 2.0 (async) + Alembic | ORM + 数据库迁移 |
| PostgreSQL | 主数据库（JSONB 存储 pipeline 状态） |
| Celery + Redis | 异步任务队列（图片/视频生成） |
| SSE (Server-Sent Events) | 实时进度推送 |
| JWT | 认证 |

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
pip install -r requirements.txt

# 复制并填写环境变量
cp .env.example .env
```

`.env` 关键配置：

```env
DATABASE_URL=postgresql+asyncpg://shakespeare:shakespeare@localhost:5432/shakespeare
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=your-random-secret-key
```

### 2. 启动数据库（Docker）

```bash
docker compose up -d postgres redis
```

### 3. 执行数据库迁移（Alembic）

```bash
alembic upgrade head
```

如遇到“历史库已存在表但未记录版本”的情况，可先打版本基线，再继续后续迁移：

```bash
alembic stamp head
```

### 4. 启动服务

```bash
# 开发模式（自动重载）
uvicorn app.main:app --reload --port 8000

# 启动 Celery Worker（图片/视频生成任务）
celery -A app.workers.celery_app worker --loglevel=info
```

访问 `http://localhost:8000/docs` 查看自动生成的 API 文档。

### 5. Docker 全量启动

```bash
docker compose up -d
```

包含：Migration + FastAPI 服务 + PostgreSQL + Redis + Celery Worker。

## 项目结构

```
shakespeare-backend/
├── app/
│   ├── main.py              # FastAPI 入口，初始化默认数据
│   ├── config.py            # 环境变量配置（pydantic-settings）
│   ├── database.py          # SQLAlchemy async 引擎
│   ├── models/              # ORM 数据模型
│   │   ├── project.py       # 项目（含 pipeline_state JSONB）
│   │   ├── novel.py         # 小说章节
│   │   ├── outline.py       # 大纲 + 故事线
│   │   ├── script.py        # 剧本
│   │   ├── storyboard.py    # 分镜
│   │   ├── asset.py         # 资产（角色/道具/场景）
│   │   ├── setting.py       # AI 配置 + Model Map + Prompts
│   │   └── task.py          # 异步任务
│   ├── schemas/
│   │   └── pipeline.py      # 状态机枚举与类型定义
│   ├── api/
│   │   ├── pipeline.py      # 核心：SSE 状态机端点
│   │   ├── auth.py          # 登录/鉴权
│   │   ├── project.py       # 项目 CRUD
│   │   ├── novel.py         # 章节上传
│   │   ├── outline.py       # 大纲查看/编辑
│   │   └── setting.py       # AI 配置管理
│   ├── agents/
│   │   ├── outline_agent.py    # 大纲 Agent（AI1→AI2→Director）
│   │   ├── script_agent.py     # 剧本生成 Agent
│   │   └── storyboard_agent.py # 分镜 Agent（Segment→Shot）
│   ├── services/
│   │   └── llm.py           # liteLLM 封装，从 DB 读取配置
│   └── prompts/
│       ├── outline.py       # 故事师/大纲师/导演 系统提示词
│       ├── script.py        # 剧本生成提示词
│       └── storyboard.py    # 分镜生成提示词
├── alembic/                 # 数据库迁移
│   └── versions/            # 版本化迁移文件（schema source of truth）
├── requirements.txt
├── docker-compose.yml
└── .env.example
```

## 状态机设计

每个项目的创作流程由六个阶段组成，每个阶段独立管理状态：

```
novel → outline → script → storyboard → images → video
```

每个阶段的状态：

| 状态 | 含义 | 前端行为 |
|------|------|---------|
| `pending` | 未开始 | 显示「生成」按钮 |
| `running` | 生成中 | 显示进度条 + SSE 实时内容 |
| `paused` | 等待确认 | 显示「确认通过」+「Chat 修改」按钮 |
| `done` | 已完成 | 显示「查看」+「Chat 优化」+「重新生成」 |
| `failed` | 失败 | 显示错误信息 + 「重试」按钮 |

重置某个阶段会连带重置所有后续依赖阶段。

## 核心 API

### Pipeline 状态机端点

```
POST /api/pipeline/{project_id}/run/{stage}
     触发阶段生成，返回 SSE 流

POST /api/pipeline/{project_id}/chat/{stage}
     阶段内 Chat 优化，返回 SSE 流
     支持：outline / script / storyboard

POST /api/pipeline/{project_id}/confirm/{stage}
     用户确认通过（paused → done）

POST /api/pipeline/{project_id}/reset/{stage}
     重置阶段（及后续依赖阶段 → pending）
```

### SSE 事件格式

```json
{"type": "progress", "stage": "outline", "progress": 45, "message": "正在生成第3集大纲..."}
{"type": "content",  "stage": "outline", "data": {"node": "storyline", "chunk": "..."}}
{"type": "pause",    "stage": "outline", "message": "生成完成，等待用户确认"}
{"type": "done",     "stage": "outline"}
{"type": "error",    "stage": "outline", "message": "错误信息"}
```

## AI 配置

### 支持的 LLM Provider

| Provider | manufacturer 值 | 说明 |
|----------|----------------|------|
| OpenAI | `openai` | GPT-4o 等 |
| Anthropic | `anthropic` | Claude 系列 |
| DeepSeek | `deepseek` | DeepSeek-V3/R1 |
| Google | `gemini` | Gemini 系列 |
| 豆包（火山引擎） | `volcengine` | 填写 base_url |
| 通义千问 | `qwen` | 填写 base_url |
| 智谱 | `zhipu` | 填写 base_url |
| xAI | `xai` | Grok 系列 |
| 自定义 | `other` | 任意 OpenAI 兼容接口 |

### Agent Key 映射

在「设置 → AI 模型映射」中将以下 key 关联到对应的 AI 配置：

| Key | 用途 |
|-----|------|
| `outlineScriptAgent` | 大纲/故事线生成（AI1 + AI2 + Director） |
| `generateScript` | 剧本生成 |
| `storyboardAgent` | 分镜生成（Segment + Shot） |
| `assetsPrompt` | 资产提示词润色 |
| `assetsImage` | 资产图片生成（图生图模型） |

## 默认账号

首次启动自动创建：

- 用户名：`admin`
- 密码：`admin123`

## 数据库迁移

```bash
# 生成迁移文件
alembic revision --autogenerate -m "描述"

# 执行迁移
alembic upgrade head
```

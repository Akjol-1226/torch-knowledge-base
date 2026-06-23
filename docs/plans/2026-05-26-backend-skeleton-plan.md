# Backend Skeleton Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 搭建火炬电子知识库 v0 后端可运行骨架（B 档），FastAPI + SQLModel + langgraph 占位 + LiteLLM 接入。

**Architecture:** feature-first 模块化结构（modules/ingest, chat, auth），核心基础设施在 core/，VLM 统一走 LiteLLM Proxy。SQLite + SQLModel 起步，asyncio queue 处理任务，TDD 推进，pytest + ruff + pyright 把关质量。

**Tech Stack:** Python 3.11+ / uv / FastAPI / SQLModel / langgraph / pydantic-settings / structlog / pytest / pytest-asyncio / httpx / openai (调 LiteLLM)

**关联**：
- 设计文档：`docs/plans/2026-05-26-backend-skeleton-design.md`
- ADR-001：`decisions/ADR-001-tech-stack.md`
- PRD v0.3：`product/prd/v0.md`

**铁律约束**（harness AGENTS.base.md）：
- 每个 task 完成后写 `progress/changes/2026-05-26-T-XXX.md`（5 段）
- 修改 contracts 必须先改 `contracts/` 再写实现
- 踩坑更新 `progress/lessons.md`

**项目环境提示**：当前项目**不是 git 仓库**，原 skill 中的 `git commit` 步骤替换为「写 changes 文档 + TaskUpdate 标完成」。

---

## Plan Review 前需用户确认的事项

| ID | 问题 | 推荐答案 | 影响 |
|---|---|---|---|
| OQ-1 | `<notsure>` 标记格式 | `<notsure>不确定内容</notsure>`（XML 风格，配合 raw_text 用正则解析） | T-010 parser 实现 |
| OQ-2 | LiteLLM 是 Proxy（HTTP）还是 SDK？ | 假设 Proxy（OpenAI 兼容），用 `openai` SDK 调用 | T-011 vlm client 实现 |
| OQ-3 | 是否预置假 seed 数据？ | 否（v0 骨架不写 seed，集成测试用 in-memory SQLite） | T-009 db.py 复杂度 |
| OQ-4 | docker-compose 是否真要 v0 用？ | 否，仅留壳供 v1 启用 | T-013 范围 |
| OQ-5 | 模块间通信约定 | modules 不直接互 import，跨模块走 `shared/contracts` | 各 module 实现纪律 |

review 后如有修改，对应 task 调整代码模板。

---

## Task 总览

| Task | 内容 | 验收锚点 | 预估 |
|---|---|---|---|
| T-006 | 项目初始化（uv + pyproject + .env.example + .gitignore） | `uv sync` 成功 | 20m |
| T-007 | core/config + core/logging | 配置加载 + 结构化日志输出 | 30m |
| T-008 | app/main + /health 端点 | curl /health 200 | 20m |
| T-009 | core/db SQLModel engine + 测试 fixture | conftest 提供干净 db session | 30m |
| T-010 | core/vlm/schemas + parser（含 `<notsure>` 解析单测） | parser 单测全绿 | 40m |
| T-011 | core/vlm/client（LiteLLM 调用） | smoke test 真实联通 | 40m |
| T-012 | modules/ingest/models（Document/Page/IngestTask） | models 单测可建可查 | 30m |
| T-013 | modules/ingest/repository | repository 单测全绿 | 30m |
| T-014 | modules/ingest/graph（langgraph 占位） | graph 状态机单测全绿 | 30m |
| T-015 | modules/ingest/service + router | POST /upload mock 成功 | 40m |
| T-016 | modules/chat 占位 router + modules/auth 占位依赖 | /chat/echo 通；auth dep 可被注入 | 20m |
| T-017 | 集成测试 + smoke test 脚本 | uv run pytest 全绿 + smoke 真调成功 | 40m |
| T-018 | docker-compose 留壳 + README 收尾 | 文档完整 | 20m |

**合计估时**：~6.5 小时（不含调 LiteLLM 时遇到网络/配置问题的余量）

---

## Task 详情

### T-006 项目初始化

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/.gitignore`
- Create: `backend/README.md`
- Create: `backend/.python-version`

**Step 1: 创建 backend/ 目录与 pyproject.toml**

```toml
# backend/pyproject.toml
[project]
name = "torch-knowledge-base-backend"
version = "0.1.0"
description = "火炬电子知识库 v0 后端"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "sqlmodel>=0.0.22",
  "pydantic>=2.9.0",
  "pydantic-settings>=2.5.0",
  "structlog>=24.4.0",
  "openai>=1.50.0",
  "httpx>=0.27.0",
  "langgraph>=0.2.0",
  "python-multipart>=0.0.12",
]

[dependency-groups]
dev = [
  "pytest>=8.3.0",
  "pytest-asyncio>=0.24.0",
  "ruff>=0.6.0",
  "pyright>=1.1.380",
  "httpx>=0.27.0",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC"]
ignore = ["S101"]  # pytest 用 assert

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
include = ["app", "tests"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: 创建 .env.example、.gitignore、.python-version**

```bash
# backend/.env.example
APP_ENV=development
LOG_LEVEL=INFO

# LiteLLM Proxy
LITELLM_BASE_URL=https://litellm.internal.futurx.cc
LITELLM_API_KEY=sk-replace-me
LITELLM_DEFAULT_VLM_MODEL=qwen-vl-max

# Database
DATABASE_URL=sqlite+aiosqlite:///./torch_kb.db
```

```bash
# backend/.gitignore
.venv/
__pycache__/
*.pyc
*.db
.env
.pytest_cache/
.ruff_cache/
.pyright/
dist/
build/
*.egg-info/
```

```bash
# backend/.python-version
3.11
```

**Step 3: 创建 README**

```markdown
# Backend (火炬电子知识库 v0)

## Quick Start

\`\`\`bash
cd backend
uv sync
cp .env.example .env  # 填好 LITELLM 三项
uv run uvicorn app.main:app --reload
\`\`\`

## Layout
详见 `../docs/plans/2026-05-26-backend-skeleton-design.md`

## Common Commands
- `uv run pytest` — 跑测试
- `uv run ruff check .` — lint
- `uv run pyright` — 类型检查
- `uv run python -m app.core.vlm.smoke_test` — 验证 LiteLLM 连通
```

**Step 4: 验证 uv sync**

Run:
```bash
cd backend && uv sync
```
Expected: `Resolved N packages` 且 `.venv/` 生成；无错误。

**Step 5: 完成动作**

- TaskUpdate T-006 → completed
- 写 `progress/changes/2026-05-26-T-006.md`（5 段）

---

### T-007 core/config + core/logging

**Files:**
- Create: `backend/app/__init__.py`（空）
- Create: `backend/app/core/__init__.py`（空）
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/logging.py`
- Test: `backend/tests/__init__.py`（空）
- Test: `backend/tests/unit/__init__.py`（空）
- Test: `backend/tests/unit/test_config.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_config.py
from app.core.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://test.local")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_DEFAULT_VLM_MODEL", "test-model")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    s = Settings()
    assert s.litellm_base_url == "http://test.local"
    assert s.litellm_api_key.get_secret_value() == "sk-test"
    assert s.litellm_default_vlm_model == "test-model"
    assert s.app_env == "development"  # 默认值


def test_settings_log_level_default():
    s = Settings()
    assert s.log_level == "INFO"
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: `ModuleNotFoundError: No module named 'app.core.config'`

**Step 3: 写实现**

```python
# backend/app/core/config.py
from functools import lru_cache
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    litellm_base_url: str
    litellm_api_key: SecretStr
    litellm_default_vlm_model: str

    database_url: str = "sqlite+aiosqlite:///./torch_kb.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# backend/app/core/logging.py
import logging
import sys
import structlog

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if settings.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: 2 passed

**Step 5: 完成动作**

- TaskUpdate T-007 → completed
- 写 `progress/changes/2026-05-26-T-007.md`

---

### T-008 app/main + /health

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/integration/__init__.py`（空）
- Test: `backend/tests/integration/test_health.py`

**Step 1: 写 failing test**

```python
# backend/tests/integration/test_health.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health_endpoint(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_openapi_schema_available(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert "/health" in schema["paths"]
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/integration/test_health.py -v`
Expected: ImportError on `app.main`

**Step 3: 写实现**

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    logger = get_logger("app.lifespan")
    logger.info("app_starting", app_env=settings.app_env)
    yield
    logger.info("app_stopped")


app = FastAPI(
    title="火炬电子知识库 v0",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/integration/test_health.py -v`
Expected: 2 passed

也验证 server 真能启动：
```bash
uv run uvicorn app.main:app --port 8000 &
sleep 2
curl localhost:8000/health
# Expected: {"status":"ok"}
kill %1
```

**Step 5: 完成动作**

- TaskUpdate T-008 → completed
- 写 `progress/changes/2026-05-26-T-008.md`

---

### T-009 core/db + 测试 fixture

**Files:**
- Create: `backend/app/core/db.py`
- Modify: `backend/tests/conftest.py`（新建）
- Test: `backend/tests/unit/test_db.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_db.py
import pytest
from sqlmodel import SQLModel, Field, select


class DummyItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str


async def test_can_create_and_query(db_session):
    item = DummyItem(name="alpha")
    db_session.add(item)
    await db_session.commit()

    result = await db_session.exec(select(DummyItem))
    items = result.all()
    assert len(items) == 1
    assert items[0].name == "alpha"
```

```python
# backend/tests/conftest.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    async with SQLModelAsyncSession(db_engine) as session:
        yield session
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_db.py -v`
Expected: 模块导入错误 / fixture 未找到

**Step 3: 写实现**

```python
# backend/app/core/db.py
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
```

也补充 main.py 在 lifespan 里调 `init_db()`：

```python
# 修改 backend/app/main.py 的 lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    logger = get_logger("app.lifespan")
    from app.core.db import init_db
    await init_db()
    logger.info("app_starting", app_env=settings.app_env)
    yield
    logger.info("app_stopped")
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_db.py -v`
Expected: 1 passed

**Step 5: 完成动作**

- TaskUpdate T-009 → completed
- 写 `progress/changes/2026-05-26-T-009.md`

---

### T-010 core/vlm/schemas + parser

**Files:**
- Create: `backend/app/core/vlm/__init__.py`
- Create: `backend/app/core/vlm/schemas.py`
- Create: `backend/app/core/vlm/parser.py`
- Test: `backend/tests/unit/test_vlm_parser.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_vlm_parser.py
from app.core.vlm.parser import parse_notsure_segments


def test_parse_empty_text():
    assert parse_notsure_segments("plain text") == []


def test_parse_single_notsure():
    text = "before <notsure>uncertain part</notsure> after"
    segments = parse_notsure_segments(text)
    assert len(segments) == 1
    assert segments[0].text == "uncertain part"
    assert text[segments[0].start:segments[0].end] == "<notsure>uncertain part</notsure>"


def test_parse_multiple_notsure():
    text = "<notsure>a</notsure> middle <notsure>b</notsure>"
    segments = parse_notsure_segments(text)
    assert [s.text for s in segments] == ["a", "b"]


def test_parse_nested_notsure_outermost_wins():
    # 嵌套时取最外层（非贪婪正则会取最短，需要测一下行为）
    text = "<notsure>outer <notsure>inner</notsure> rest</notsure>"
    segments = parse_notsure_segments(text)
    # 实现选择：贪婪取最外层，整体一段
    assert len(segments) == 1
    assert "outer" in segments[0].text and "rest" in segments[0].text


def test_parse_multiline_notsure():
    text = "<notsure>line1\nline2</notsure>"
    segments = parse_notsure_segments(text)
    assert segments[0].text == "line1\nline2"
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_vlm_parser.py -v`
Expected: ImportError

**Step 3: 写实现**

```python
# backend/app/core/vlm/schemas.py
from pydantic import BaseModel


class NotsureSpan(BaseModel):
    start: int
    end: int
    text: str


class VLMResponse(BaseModel):
    raw_text: str
    notsure_segments: list[NotsureSpan]
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
```

```python
# backend/app/core/vlm/parser.py
import re

from app.core.vlm.schemas import NotsureSpan

# 贪婪匹配，嵌套时取最外层，跨行用 DOTALL
_NOTSURE_RE = re.compile(r"<notsure>(.*)</notsure>", re.DOTALL)
# 但默认贪婪在多段时会吃掉中间，需用非贪婪 + 平衡处理
_NOTSURE_NONGREEDY_RE = re.compile(r"<notsure>(.*?)</notsure>", re.DOTALL)


def parse_notsure_segments(text: str) -> list[NotsureSpan]:
    """解析 <notsure>...</notsure> 标记，返回所有片段。

    嵌套情况下取最外层（贪婪策略）；多个独立 notsure 段返回多段。
    """
    if "<notsure>" not in text:
        return []

    segments: list[NotsureSpan] = []
    # 找平衡的最外层匹配：用栈式扫描
    stack: list[int] = []
    pos = 0
    open_tag = "<notsure>"
    close_tag = "</notsure>"

    while pos < len(text):
        next_open = text.find(open_tag, pos)
        next_close = text.find(close_tag, pos)

        if next_open == -1 and next_close == -1:
            break

        if next_open != -1 and (next_close == -1 or next_open < next_close):
            stack.append(next_open)
            pos = next_open + len(open_tag)
        else:
            if stack:
                start = stack.pop()
                if not stack:  # 最外层闭合
                    end = next_close + len(close_tag)
                    inner = text[start + len(open_tag): next_close]
                    segments.append(NotsureSpan(start=start, end=end, text=inner))
            pos = next_close + len(close_tag)

    return segments
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_vlm_parser.py -v`
Expected: 5 passed

**Step 5: 完成动作**

- TaskUpdate T-010 → completed
- 写 `progress/changes/2026-05-26-T-010.md`

---

### T-011 core/vlm/client（LiteLLM 调用）

**Files:**
- Create: `backend/app/core/vlm/client.py`
- Create: `backend/app/core/vlm/smoke_test.py`
- Test: `backend/tests/unit/test_vlm_client.py`

**Step 1: 写 failing test（mock LiteLLM 响应）**

```python
# backend/tests/unit/test_vlm_client.py
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.vlm.client import VLMClient


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.litellm_base_url = "http://fake"
    s.litellm_api_key.get_secret_value.return_value = "sk-fake"
    s.litellm_default_vlm_model = "fake-vlm"
    return s


async def test_extract_page_returns_vlm_response(fake_settings):
    client = VLMClient(fake_settings)

    fake_message = MagicMock()
    fake_message.content = "extracted text with <notsure>doubt</notsure>"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_usage = MagicMock()
    fake_usage.prompt_tokens = 10
    fake_usage.completion_tokens = 20
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = fake_usage
    fake_response.model = "fake-vlm"

    client.client.chat.completions.create = AsyncMock(return_value=fake_response)

    result = await client.extract_page(
        image_bytes=b"\x89PNG fake",
        prompt="describe page",
    )

    assert result.raw_text == "extracted text with <notsure>doubt</notsure>"
    assert len(result.notsure_segments) == 1
    assert result.notsure_segments[0].text == "doubt"
    assert result.model_id == "fake-vlm"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20


async def test_extract_page_uses_custom_model(fake_settings):
    client = VLMClient(fake_settings)

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="x"))]
    fake_response.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
    fake_response.model = "custom"

    client.client.chat.completions.create = AsyncMock(return_value=fake_response)

    await client.extract_page(image_bytes=b"x", prompt="y", model="override-model")

    call_kwargs = client.client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "override-model"
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_vlm_client.py -v`
Expected: ImportError

**Step 3: 写实现**

```python
# backend/app/core/vlm/client.py
import base64
import time

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.vlm.parser import parse_notsure_segments
from app.core.vlm.schemas import VLMResponse

logger = get_logger("vlm.client")


class VLMClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key.get_secret_value(),
        )
        self.default_model = settings.litellm_default_vlm_model

    async def extract_page(
        self,
        image_bytes: bytes,
        prompt: str,
        model: str | None = None,
    ) -> VLMResponse:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        target_model = model or self.default_model

        t0 = time.perf_counter()
        response = await self.client.chat.completions.create(
            model=target_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        raw_text = response.choices[0].message.content or ""
        usage = response.usage

        logger.info(
            "vlm_extract_page",
            model=target_model,
            latency_ms=latency_ms,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            notsure_count=raw_text.count("<notsure>"),
        )

        return VLMResponse(
            raw_text=raw_text,
            notsure_segments=parse_notsure_segments(raw_text),
            model_id=response.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            latency_ms=latency_ms,
        )
```

```python
# backend/app/core/vlm/smoke_test.py
"""手动 smoke test：验证 LiteLLM 真实连通。

Run: uv run python -m app.core.vlm.smoke_test
需要 .env 配齐 LITELLM_* 三项。
"""

import asyncio
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.vlm.client import VLMClient

logger = get_logger("vlm.smoke")


async def main():
    settings = get_settings()
    configure_logging(settings)

    img_path = Path(__file__).parent / "fixtures" / "smoke.png"
    if not img_path.exists():
        # 用一张 1x1 透明 PNG 占位
        import base64
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        img_path.parent.mkdir(exist_ok=True)
        img_path.write_bytes(png_bytes)

    client = VLMClient(settings)
    resp = await client.extract_page(
        image_bytes=img_path.read_bytes(),
        prompt="描述这张图。如果有不确定的部分，用 <notsure>...</notsure> 标记。",
    )

    logger.info(
        "smoke_test_ok",
        model=resp.model_id,
        latency_ms=resp.latency_ms,
        tokens=resp.prompt_tokens + resp.completion_tokens,
        raw_text_preview=resp.raw_text[:200],
    )
    print(f"\n=== Smoke Test OK ===")
    print(f"Model: {resp.model_id}")
    print(f"Latency: {resp.latency_ms}ms")
    print(f"Raw text:\n{resp.raw_text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_vlm_client.py -v`
Expected: 2 passed

真实 smoke（需 .env 配齐）：
```bash
uv run python -m app.core.vlm.smoke_test
```
Expected: 打印 `=== Smoke Test OK ===` + model/latency/raw_text

**Step 5: 完成动作**

- TaskUpdate T-011 → completed
- 写 `progress/changes/2026-05-26-T-011.md`

---

### T-012 modules/ingest/models

**Files:**
- Create: `backend/app/modules/__init__.py`（空）
- Create: `backend/app/modules/ingest/__init__.py`（空）
- Create: `backend/app/modules/ingest/models.py`
- Test: `backend/tests/unit/test_ingest_models.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_ingest_models.py
from datetime import datetime
from sqlmodel import select

from app.modules.ingest.models import Document, IngestTask, Page


async def test_document_round_trip(db_session):
    doc = Document(
        filename="test.pdf",
        sha256="abc123",
        page_count=10,
        created_at=datetime.utcnow(),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    result = await db_session.exec(select(Document).where(Document.sha256 == "abc123"))
    found = result.first()
    assert found is not None
    assert found.filename == "test.pdf"


async def test_page_belongs_to_document(db_session):
    doc = Document(filename="x.pdf", sha256="hash1", page_count=2, created_at=datetime.utcnow())
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    page = Page(document_id=doc.id, page_number=1, raw_text="hello")
    db_session.add(page)
    await db_session.commit()
    await db_session.refresh(page)

    assert page.document_id == doc.id


async def test_ingest_task_default_status(db_session):
    doc = Document(filename="y.pdf", sha256="hash2", page_count=1, created_at=datetime.utcnow())
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    task = IngestTask(
        document_id=doc.id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.status == "pending"
    assert task.progress == 0
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_ingest_models.py -v`
Expected: ImportError

**Step 3: 写实现**

```python
# backend/app/modules/ingest/models.py
from datetime import datetime

from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    sha256: str = Field(index=True, unique=True)
    page_count: int
    created_at: datetime


class Page(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    page_number: int
    raw_text: str | None = None
    notsure_count: int = 0
    extracted_at: datetime | None = None


class IngestTask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    status: str = Field(default="pending", index=True)
    progress: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_ingest_models.py -v`
Expected: 3 passed

**Step 5: 完成动作**

- TaskUpdate T-012 → completed
- 写 `progress/changes/2026-05-26-T-012.md`

---

### T-013 modules/ingest/repository

**Files:**
- Create: `backend/app/modules/ingest/repository.py`
- Test: `backend/tests/unit/test_ingest_repository.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_ingest_repository.py
from datetime import datetime

from app.modules.ingest.models import Document
from app.modules.ingest.repository import IngestRepository


async def test_create_document_returns_id(db_session):
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h1", page_count=5)
    assert doc.id is not None
    assert doc.filename == "a.pdf"


async def test_find_document_by_sha256(db_session):
    repo = IngestRepository(db_session)
    await repo.create_document(filename="a.pdf", sha256="hash_x", page_count=5)

    found = await repo.find_document_by_sha256("hash_x")
    assert found is not None
    assert found.filename == "a.pdf"

    missing = await repo.find_document_by_sha256("nope")
    assert missing is None


async def test_create_ingest_task(db_session):
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h2", page_count=5)

    task = await repo.create_ingest_task(document_id=doc.id)
    assert task.status == "pending"
    assert task.document_id == doc.id


async def test_update_task_status(db_session):
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h3", page_count=5)
    task = await repo.create_ingest_task(document_id=doc.id)

    updated = await repo.update_task_status(task.id, status="running", progress=50)
    assert updated.status == "running"
    assert updated.progress == 50
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_ingest_repository.py -v`
Expected: ImportError

**Step 3: 写实现**

```python
# backend/app/modules/ingest/repository.py
from datetime import datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ingest.models import Document, IngestTask, Page


class IngestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_document(
        self, filename: str, sha256: str, page_count: int
    ) -> Document:
        doc = Document(
            filename=filename,
            sha256=sha256,
            page_count=page_count,
            created_at=datetime.utcnow(),
        )
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        result = await self.session.exec(
            select(Document).where(Document.sha256 == sha256)
        )
        return result.first()

    async def create_ingest_task(self, document_id: int) -> IngestTask:
        now = datetime.utcnow()
        task = IngestTask(
            document_id=document_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_task_status(
        self,
        task_id: int,
        *,
        status: str | None = None,
        progress: int | None = None,
        error: str | None = None,
    ) -> IngestTask:
        task = await self.session.get(IngestTask, task_id)
        if task is None:
            raise ValueError(f"IngestTask {task_id} not found")
        if status is not None:
            task.status = status
        if progress is not None:
            task.progress = progress
        if error is not None:
            task.error = error
        task.updated_at = datetime.utcnow()
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def list_tasks(self, status: str | None = None) -> list[IngestTask]:
        stmt = select(IngestTask)
        if status:
            stmt = stmt.where(IngestTask.status == status)
        result = await self.session.exec(stmt)
        return list(result.all())
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_ingest_repository.py -v`
Expected: 4 passed

**Step 5: 完成动作**

- TaskUpdate T-013 → completed
- 写 `progress/changes/2026-05-26-T-013.md`

---

### T-014 modules/ingest/graph（langgraph 占位）

**Files:**
- Create: `backend/app/modules/ingest/graph.py`
- Test: `backend/tests/unit/test_ingest_graph.py`

**Step 1: 写 failing test**

```python
# backend/tests/unit/test_ingest_graph.py
from app.modules.ingest.graph import IngestState, build_ingest_graph


async def test_graph_runs_through_all_nodes():
    graph = build_ingest_graph()
    initial: IngestState = {
        "document_id": 1,
        "pages_done": 0,
        "pages_total": 10,
        "errors": [],
        "trace": [],
    }
    final = await graph.ainvoke(initial)

    assert final["trace"] == [
        "load_pdf",
        "extract_pages",
        "build_pageindex",
        "persist",
    ]
    assert final["errors"] == []


async def test_graph_state_preserved():
    graph = build_ingest_graph()
    initial: IngestState = {
        "document_id": 42,
        "pages_done": 0,
        "pages_total": 5,
        "errors": [],
        "trace": [],
    }
    final = await graph.ainvoke(initial)
    assert final["document_id"] == 42
    assert final["pages_total"] == 5
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/unit/test_ingest_graph.py -v`
Expected: ImportError

**Step 3: 写实现**

```python
# backend/app/modules/ingest/graph.py
"""入库流水线状态图（占位实现）。

v0 骨架：节点全部 print/log 自己被调用，便于联调测通框架。
真实实现（PDF 拆页 + VLM 抽取 + PageIndex 构建 + DB 持久化）等 ADR-002
（VLM 厂商）拍板 + PoC 完成后再补，挂到本图的同名节点上。
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger

logger = get_logger("ingest.graph")


class IngestState(TypedDict):
    document_id: int
    pages_done: int
    pages_total: int
    errors: list[str]
    trace: list[str]  # 调试用：记录走过的节点


async def _placeholder_load_pdf(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="load_pdf", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "load_pdf"]}


async def _placeholder_extract_pages(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="extract_pages", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "extract_pages"]}


async def _placeholder_build_pageindex(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="build_pageindex", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "build_pageindex"]}


async def _placeholder_persist(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="persist", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "persist"]}


def build_ingest_graph():
    g = StateGraph(IngestState)
    g.add_node("load_pdf", _placeholder_load_pdf)
    g.add_node("extract_pages", _placeholder_extract_pages)
    g.add_node("build_pageindex", _placeholder_build_pageindex)
    g.add_node("persist", _placeholder_persist)

    g.set_entry_point("load_pdf")
    g.add_edge("load_pdf", "extract_pages")
    g.add_edge("extract_pages", "build_pageindex")
    g.add_edge("build_pageindex", "persist")
    g.add_edge("persist", END)
    return g.compile()
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/unit/test_ingest_graph.py -v`
Expected: 2 passed

**Step 5: 完成动作**

- TaskUpdate T-014 → completed
- 写 `progress/changes/2026-05-26-T-014.md`

---

### T-015 modules/ingest/service + router

**Files:**
- Create: `backend/app/modules/ingest/schemas.py`（API request/response）
- Create: `backend/app/modules/ingest/service.py`
- Create: `backend/app/modules/ingest/router.py`
- Modify: `backend/app/main.py`（include router）
- Test: `backend/tests/integration/test_ingest_router.py`

**Step 1: 写 failing integration test**

```python
# backend/tests/integration/test_ingest_router.py
import hashlib

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(db_engine):
    # 用 in-memory db 替换全局
    from app.core import db as db_mod
    from app.main import app

    db_mod._engine = db_engine
    db_mod._session_factory = None  # 触发重建

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_upload_creates_task(client):
    fake_pdf = b"%PDF-1.4 fake content for test\n"
    files = {"file": ("test.pdf", fake_pdf, "application/pdf")}
    r = await client.post("/ingest/upload", files=files)

    assert r.status_code == 200
    payload = r.json()
    assert "task_id" in payload
    assert payload["status"] == "pending"
    assert payload["filename"] == "test.pdf"


async def test_list_tasks_returns_created(client):
    fake_pdf = b"%PDF-1.4 another fake\n"
    files = {"file": ("a.pdf", fake_pdf, "application/pdf")}
    await client.post("/ingest/upload", files=files)

    r = await client.get("/ingest/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) >= 1
    assert any(t["filename"] == "a.pdf" for t in tasks)
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/integration/test_ingest_router.py -v`
Expected: 404 / 模块未挂

**Step 3: 写实现**

```python
# backend/app/modules/ingest/schemas.py
from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    task_id: int
    document_id: int
    filename: str
    status: str
    page_count: int


class TaskItem(BaseModel):
    task_id: int
    document_id: int
    filename: str
    status: str
    progress: int
    error: str | None
    created_at: datetime
    updated_at: datetime
```

```python
# backend/app/modules/ingest/service.py
import hashlib

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import get_logger
from app.modules.ingest.graph import build_ingest_graph
from app.modules.ingest.repository import IngestRepository
from app.modules.ingest.schemas import TaskItem, UploadResponse

logger = get_logger("ingest.service")


class IngestService:
    def __init__(self, session: AsyncSession):
        self.repo = IngestRepository(session)

    async def upload_document(
        self, filename: str, content: bytes
    ) -> UploadResponse:
        sha256 = hashlib.sha256(content).hexdigest()
        existing = await self.repo.find_document_by_sha256(sha256)
        if existing:
            logger.info("upload_duplicate", filename=filename, sha256=sha256)
            doc = existing
        else:
            # v0 占位：PDF 页数解析未实现，固定 0
            doc = await self.repo.create_document(
                filename=filename, sha256=sha256, page_count=0
            )

        task = await self.repo.create_ingest_task(document_id=doc.id)

        logger.info(
            "ingest_task_created",
            task_id=task.id,
            document_id=doc.id,
            filename=filename,
        )

        return UploadResponse(
            task_id=task.id,
            document_id=doc.id,
            filename=doc.filename,
            status=task.status,
            page_count=doc.page_count,
        )

    async def list_tasks(self) -> list[TaskItem]:
        tasks = await self.repo.list_tasks()
        items: list[TaskItem] = []
        for t in tasks:
            doc = await self.repo.session.get(
                __import__("app.modules.ingest.models", fromlist=["Document"]).Document,
                t.document_id,
            )
            items.append(
                TaskItem(
                    task_id=t.id,
                    document_id=t.document_id,
                    filename=doc.filename if doc else "",
                    status=t.status,
                    progress=t.progress,
                    error=t.error,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
            )
        return items
```

```python
# backend/app/modules/ingest/router.py
from fastapi import APIRouter, Depends, File, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_db
from app.modules.ingest.schemas import TaskItem, UploadResponse
from app.modules.ingest.service import IngestService

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    content = await file.read()
    service = IngestService(session)
    return await service.upload_document(filename=file.filename or "unknown", content=content)


@router.get("/tasks", response_model=list[TaskItem])
async def list_tasks(session: AsyncSession = Depends(get_db)):
    service = IngestService(session)
    return await service.list_tasks()
```

修改 `backend/app/main.py`，挂载 router：

```python
# 在 main.py 末尾添加
from app.modules.ingest.router import router as ingest_router
app.include_router(ingest_router)
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/integration/test_ingest_router.py -v`
Expected: 2 passed

**Step 5: 完成动作**

- TaskUpdate T-015 → completed
- 写 `progress/changes/2026-05-26-T-015.md`

---

### T-016 modules/chat 占位 + modules/auth 占位

**Files:**
- Create: `backend/app/modules/chat/__init__.py`（空）
- Create: `backend/app/modules/chat/router.py`
- Create: `backend/app/modules/auth/__init__.py`（空）
- Create: `backend/app/modules/auth/dependencies.py`
- Modify: `backend/app/main.py`（include chat router）
- Test: `backend/tests/integration/test_chat_echo.py`

**Step 1: 写 failing test**

```python
# backend/tests/integration/test_chat_echo.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_chat_echo(client):
    r = await client.post("/chat/echo", json={"message": "hello"})
    assert r.status_code == 200
    assert r.json() == {"echo": "hello"}
```

**Step 2: 跑 test 验证失败**

Run: `uv run pytest tests/integration/test_chat_echo.py -v`
Expected: 404

**Step 3: 写实现**

```python
# backend/app/modules/chat/router.py
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/chat", tags=["chat"])


class EchoRequest(BaseModel):
    message: str


class EchoResponse(BaseModel):
    echo: str


@router.post("/echo", response_model=EchoResponse)
async def echo(req: EchoRequest):
    """v0 占位 endpoint。真实聊天/检索/SSE 流式响应在后续 task 实现。"""
    return EchoResponse(echo=req.message)
```

```python
# backend/app/modules/auth/dependencies.py
"""权限依赖注入框架占位。

v0 骨架阶段：返回一个固定的 anonymous user，方便其他模块直接 Depends(current_user)
而不被未实现的鉴权挡住。
真实 RBAC 实现等 PRD 权限模块 task。
"""

from pydantic import BaseModel


class CurrentUser(BaseModel):
    id: int = 0
    username: str = "anonymous"
    is_admin: bool = False


async def get_current_user() -> CurrentUser:
    return CurrentUser()
```

修改 `backend/app/main.py`：
```python
from app.modules.chat.router import router as chat_router
app.include_router(chat_router)
```

**Step 4: 跑 test 验证通过**

Run: `uv run pytest tests/integration/test_chat_echo.py -v`
Expected: 1 passed

**Step 5: 完成动作**

- TaskUpdate T-016 → completed
- 写 `progress/changes/2026-05-26-T-016.md`

---

### T-017 全量测试 + smoke test 真调

**Step 1: 跑全量测试套件**

```bash
cd backend
uv run pytest -v
```
Expected: 全部用例 PASS（至少 17 个：config 2 + db 1 + vlm parser 5 + vlm client 2 + ingest models 3 + ingest repo 4 + ingest graph 2 + ingest router 2 + chat echo 1 + health 2）

**Step 2: 跑 lint + type check**

```bash
uv run ruff check .
uv run pyright
```
Expected: 0 错误。如果有警告，逐个修复或在 ruff/pyright 配置加白名单（仅限合理理由）。

**Step 3: 真调 LiteLLM smoke test**

```bash
cp .env.example .env
# 编辑 .env，填好 LITELLM_BASE_URL / LITELLM_API_KEY / LITELLM_DEFAULT_VLM_MODEL
uv run python -m app.core.vlm.smoke_test
```
Expected: 打印 `=== Smoke Test OK ===` + 真实模型 ID + raw_text。

如果失败：
- 检查 .env 配置
- 检查 LiteLLM Proxy 是否需要 `metadata` / `user` 等额外字段
- 看 structlog 输出的 error 详情

**Step 4: 启动 server 验证全链路**

```bash
uv run uvicorn app.main:app --port 8000 &
sleep 2

# Health
curl localhost:8000/health
# Expected: {"status":"ok"}

# OpenAPI
curl localhost:8000/openapi.json | python -m json.tool | head -50
# Expected: 看到 /health /ingest/upload /ingest/tasks /chat/echo

# Upload
echo "fake pdf" > /tmp/test.pdf
curl -F file=@/tmp/test.pdf localhost:8000/ingest/upload
# Expected: 返回 task_id + status=pending

# Tasks
curl localhost:8000/ingest/tasks
# Expected: 列表含刚才上传的任务

# Chat echo
curl -X POST localhost:8000/chat/echo -H 'Content-Type: application/json' -d '{"message":"hi"}'
# Expected: {"echo":"hi"}

kill %1
```

**Step 5: 完成动作**

- TaskUpdate T-017 → completed
- 写 `progress/changes/2026-05-26-T-017.md`

---

### T-018 docker-compose 留壳 + README 收尾 + lessons 更新

**Files:**
- Create: `backend/docker-compose.yml`
- Create: `backend/Dockerfile`
- Modify: `backend/README.md`
- Modify: `progress/lessons.md`（如有踩坑）
- Modify: `progress/current.md`
- Modify: `progress/code-map.md`

**Step 1: 写 docker-compose 壳**

```yaml
# backend/docker-compose.yml
# v0 骨架阶段：留壳供 v1 启用。SQLite 单机不需要外部服务。
# v1+ 加入 Postgres / Redis 时在此扩展。
services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./torch_kb.db:/app/torch_kb.db
```

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 2: 更新 README，补全 layout 与命令清单**

```markdown
# backend/README.md（更新版）
# Backend (火炬电子知识库 v0)

骨架完成度：B 档（详见 `../docs/plans/2026-05-26-backend-skeleton-design.md`）

## Quick Start

```bash
cd backend
uv sync
cp .env.example .env  # 填好 LITELLM_BASE_URL / LITELLM_API_KEY / LITELLM_DEFAULT_VLM_MODEL
uv run uvicorn app.main:app --reload
```

## Common Commands

| 命令 | 说明 |
|---|---|
| `uv run pytest -v` | 全量单测 + 集成 |
| `uv run pytest tests/unit -v` | 仅单测 |
| `uv run ruff check .` | lint |
| `uv run ruff format .` | format |
| `uv run pyright` | 类型检查 |
| `uv run python -m app.core.vlm.smoke_test` | 验证 LiteLLM 连通 |
| `uv run uvicorn app.main:app --reload` | 启动 dev server |

## Layout

```
app/
├── main.py              # FastAPI 入口 + lifespan
├── core/                # 基础设施
│   ├── config.py        # pydantic-settings
│   ├── db.py            # SQLModel engine
│   ├── logging.py       # structlog
│   └── vlm/             # LiteLLM 调用层
└── modules/
    ├── ingest/          # 入库（含 langgraph 占位）
    ├── chat/            # 聊天（占位 echo）
    └── auth/            # 权限（占位依赖）
```

## 关键文件
- `core/vlm/client.py`：统一 LiteLLM 调用，模型走 config 注入
- `modules/ingest/graph.py`：langgraph 占位状态图（4 节点）
- `tests/conftest.py`：in-memory SQLite + db_session fixture
```

**Step 3: 更新 progress/current.md**

把项目阶段从「PRD v0.3 + 技术栈定型」推进到「后端骨架完成，进入实际入库流水线开发」。

**Step 4: 更新 progress/code-map.md**

```markdown
# progress/code-map.md（更新版）
# 代码地图

## backend/
- 入口：`backend/app/main.py`
- 配置：`backend/app/core/config.py`（pydantic-settings + .env）
- 数据库：`backend/app/core/db.py`（SQLModel + SQLite）
- VLM：`backend/app/core/vlm/`（client/parser/schemas）
- 业务模块：`backend/app/modules/{ingest,chat,auth}/`

## docs/
- 设计文档：`docs/plans/2026-05-26-backend-skeleton-design.md`
- 实施计划：`docs/plans/2026-05-26-backend-skeleton-plan.md`

## product/
- PRD：`product/prd/v0.md`（v0.3）
- mockup：`product/design/v0-mockup.html`

## decisions/
- ADR-001 技术栈选型：`decisions/ADR-001-tech-stack.md`
```

**Step 5: 完成动作**

- TaskUpdate T-018 → completed
- 写 `progress/changes/2026-05-26-T-018.md`
- 更新 `progress/lessons.md`（如本次有踩坑）

---

## 全 plan 执行后的最终验收

| 检查项 | 命令 / 动作 | 期望 |
|---|---|---|
| 项目可启动 | `uv run uvicorn app.main:app` | 监听 8000 |
| 全部测试通过 | `uv run pytest -v` | 17+ tests passed |
| Lint 零警告 | `uv run ruff check .` | 0 issues |
| 类型零错误 | `uv run pyright` | 0 errors |
| LiteLLM 真调 | `uv run python -m app.core.vlm.smoke_test` | `=== Smoke Test OK ===` |
| OpenAPI 完整 | `curl localhost:8000/openapi.json` | 含 4 个 endpoint |
| Upload 闭环 | `curl -F file=@x.pdf /ingest/upload` | 200 + task_id |
| 18 份 changes 文档 | `ls progress/changes/2026-05-26-T-0{06..18}.md` | 13 个文件存在 |

---

## 风险监控点（执行中遇到立即停下）

1. **LiteLLM 接入失败**：可能是 base_url / api_key / model 名错，或者 Proxy 假设不成立（实际是 SDK 直连）。停下询问用户，不要瞎调
2. **SQLModel async session API 与文档不一致**：sqlmodel 2025+ 的 async 用法仍在演进，遇到 deprecated warning 时记录到 lessons.md
3. **langgraph 0.2+ API 变化**：`StateGraph` 接口可能改名，import 失败时查 context7 文档
4. **pyright 误报**：第三方库 stub 缺失时不要为了通过加 `# type: ignore`，先看是不是真问题

---

## 执行方式选项

Plan complete and saved to `docs/plans/2026-05-26-backend-skeleton-plan.md`.

两种执行方式：

**1. Subagent-Driven（本会话内）**
- 我每个 task 派一个 fresh subagent 实施
- task 之间我做 code review
- 快速迭代，但占本会话 context

**2. Parallel Session（新会话）**
- 你打开新会话，使用 `superpowers:executing-plans` skill
- 按 task 批量执行 + checkpoint review
- 不占本会话 context

哪种？

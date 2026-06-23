# Backend Skeleton 设计文档（v0 B 档）

| 项 | 内容 |
|---|---|
| 日期 | 2026-05-26 |
| 关联任务 | T-006 ~ T-021（实施 plan 见 sibling 文件） |
| 来源 | 本会话 brainstorming 收口（Q1-Q5） |
| 关联 ADR | ADR-001（技术栈选型） |
| 关联 PRD | `product/prd/v0.md` v0.3 |

---

## 目标

搭建火炬电子知识库 v0 后端的可运行骨架（B 档），让团队后续业务任务可以"开箱即写"。

**不在范围**：
- 真实 PageIndex 树构建逻辑
- 真实入库流水线（VLM 节点占位）
- Review 流程
- 真实 RBAC 鉴权（auth 模块只放依赖注入框架）
- 聊天模块只放 echo 占位 router

---

## 已收口的 5 个决策（brainstorming Q1-Q5）

### D-1 骨架范围 = B 档

**包含**：FastAPI app / SQLite + SQLModel / VLMClient / langgraph 占位状态图 / asyncio task queue / OpenAPI 导出。

**不含**：真业务流水线。占位完成的标准是「跑通 pytest + 启动 server + 调 LiteLLM 一次成功」。

### D-2 工具链层默认套餐

| 项 | 选择 |
|---|---|
| Python | 3.11+ |
| 包管理 | uv |
| Lint / Format | ruff |
| Type Check | pyright |
| Test | pytest + pytest-asyncio |
| HTTP client | httpx |
| 配置 | pydantic-settings |
| 日志 | structlog |
| API schema | FastAPI 自带 OpenAPI |

### D-3 目录结构 = feature-first（按业务模块切）

```
backend/
├── app/
│   ├── main.py
│   ├── core/                # 跨模块基础设施
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── logging.py
│   │   └── vlm/             # VLM 客户端（不属于任何业务模块）
│   ├── modules/
│   │   ├── ingest/          # 入库模块
│   │   ├── chat/            # 聊天模块（占位）
│   │   └── auth/            # 权限模块（占位）
│   └── shared/
├── tests/
│   ├── unit/
│   └── integration/
├── pyproject.toml
├── .env.example
└── docker-compose.yml       # 留壳
```

**纪律**：modules 之间不能直接 import 对方私有代码，跨模块通过 `shared/` 或 `contracts/` 暴露的接口通信。

### D-4 ORM = SQLModel

理由：v0 数据规模小（< 10 万行）、PRD 数据模型简单、与 pydantic v2 同源减重复。

**逃生方案**：复杂查询时直接降到 SQLAlchemy 原生 API（SQLModel 底层就是它）。

### D-5 VLM 接入 = 统一调 LiteLLM（团队已有基础设施）

**关键改变**：原 ADR-001 D-1 设计的「云/本地 adapter 多实现」简化为「单实现 + config 驱动模型选择」。

**接入假设**（待用户在 plan review 时确认）：

- LiteLLM 是 **Proxy 服务**（HTTP，OpenAI 兼容接口）
- 调用方式：`openai` Python SDK 配 `base_url` + `api_key` 指向 LiteLLM Proxy
- 模型名通过 config 注入（不在代码硬编码）
- 配置三项：`LITELLM_BASE_URL` / `LITELLM_API_KEY` / `LITELLM_DEFAULT_VLM_MODEL`

如假设不成立（例如其实是直接用 LiteLLM Python SDK），plan 阶段纠正，代码层调整。

---

## 关键接口预览

### VLMClient

```python
# app/core/vlm/client.py
class VLMClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
        self.default_model = settings.litellm_default_vlm_model

    async def extract_page(
        self,
        image_bytes: bytes,
        prompt: str,
        model: str | None = None,
    ) -> VLMResponse: ...
```

```python
# app/core/vlm/schemas.py
class NotsureSpan(BaseModel):
    start: int   # 在 raw_text 的字符偏移
    end: int
    text: str    # 原文片段

class VLMResponse(BaseModel):
    raw_text: str
    notsure_segments: list[NotsureSpan]
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
```

### Ingest models

```python
# app/modules/ingest/models.py
class Document(SQLModel, table=True):
    id: int | None = Field(primary_key=True)
    filename: str
    sha256: str = Field(index=True, unique=True)
    page_count: int
    created_at: datetime

class Page(SQLModel, table=True):
    id: int | None = Field(primary_key=True)
    document_id: int = Field(foreign_key="document.id")
    page_number: int
    raw_text: str | None = None              # VLM 输出（含 notsure）
    notsure_count: int = 0
    extracted_at: datetime | None = None

class IngestTask(SQLModel, table=True):
    id: int | None = Field(primary_key=True)
    document_id: int = Field(foreign_key="document.id")
    status: str = Field(default="pending")   # pending/running/done/failed/needs_review
    progress: int = 0                        # 0-100
    error: str | None = None
    created_at: datetime
    updated_at: datetime
```

### Ingest langgraph 状态图（占位）

```python
# app/modules/ingest/graph.py
# v0 骨架：节点全部占位，print/log 自己被调用
# 真实实现等 ADR-002（VLM 厂商）+ PoC 后

class IngestState(TypedDict):
    document_id: int
    pages_done: int
    pages_total: int
    errors: list[str]

def build_ingest_graph() -> StateGraph:
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

---

## 验收标准

| 检查项 | 命令 / 动作 | 期望 |
|---|---|---|
| 依赖安装 | `uv sync` | 0 错误 |
| 服务启动 | `uv run uvicorn app.main:app --reload` | 监听 8000 端口 |
| 健康检查 | `curl localhost:8000/health` | `{"status":"ok"}` |
| OpenAPI 导出 | `curl localhost:8000/openapi.json` | 完整 schema，含 `/ingest/upload` |
| Upload 路由（mock） | `curl -F file=@test.pdf localhost:8000/ingest/upload` | 返回 task_id + status=pending |
| LiteLLM 连通 | `uv run python -m app.core.vlm.smoke_test` | 成功调用并打印 raw_text |
| 单元测试 | `uv run pytest tests/unit -v` | 全绿，覆盖 vlm parser + ingest graph |
| 集成测试 | `uv run pytest tests/integration -v` | 全绿，覆盖 /ingest/upload 真实流程 |
| Lint | `uv run ruff check .` | 0 警告 |
| Type check | `uv run pyright` | 0 错误 |

---

## 边界与风险

### 显式不做（v0 骨架阶段）

| 项 | 推迟到 |
|---|---|
| 真实 PageIndex 树构建 | T-022+ 真实入库流水线 |
| 真实 RBAC 鉴权 | T-030+ 权限模块 |
| Migration 工具（alembic） | 第一次 schema 变更时 |
| Postgres 切换 | ADR-003 拍板后 |
| 文件存储（OSS / 本地盘） | 真实入库流水线时 |
| Celery / 分布式队列 | v1+ |

### 已识别风险

| 风险 | 缓解 |
|---|---|
| LiteLLM Proxy 假设不成立（实际是 SDK） | plan review 阶段确认；纠错代价低（只改 `vlm/client.py`） |
| SQLModel 在递归查询上不够用（PageIndex 树） | 必要时局部跌出到 SQLAlchemy 原生 API |
| 双语言栈类型同步漂移 | OpenAPI codegen Day 1 接 CI（v0 之后） |
| `<notsure>` 标记格式自由发挥 | parser 用正则严格定义（plan 中确定具体格式） |

### Open Questions（plan review 时拍板）

- **OQ-1**：`<notsure>` 标记是 XML 风格 `<notsure>...</notsure>` 还是 markdown 风格 `[?...?]`？影响 parser 实现
- **OQ-2**：LiteLLM Proxy 是否要求传 `metadata` / `user` 等额外字段做计费归集？
- **OQ-3**：骨架阶段是否要预生成一个 mini SQLite seed（一份假 Document + Pages）方便联调？

---

## 后续 ADR 触发点

- **ADR-002**：VLM 厂商选型 — 抽样 PoC 后写
- **ADR-003**：BM25 后端（SQLite FTS5 vs Postgres tsvector） — 数据规模实测后写
- **ADR-004**：langgraph state schema 标准化 — 真实入库流水线第一次 review 时写

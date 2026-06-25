# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

火炬电子（Torch Electronics）研发工程师查工艺/检验文件的内部知识库问答系统。

v0 三大功能：PDF/Word/PPT 入库 → PageIndex 树形检索 → 聊天问答；含业务角色权限。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 / FastAPI / SQLModel / langgraph / pydantic v2 |
| 前端 | Next.js 14 / TypeScript / Tailwind / shadcn/ui（待 Sprint-0 完成） |
| 数据库 | SQLite (v0) / Postgres (v1+) |
| VLM | 统一走团队 LiteLLM Proxy（OpenAI 兼容） |
| 包管理 | uv (backend) / pnpm (frontend) |
| Lint / Type | ruff + pyright |

## 项目结构

```
backend/                # 后端（目前唯一代码区，feature-first）
  app/
    core/               # 基础设施：config / db / logging / fsutil / docparse（PDF→md VLM 流水线）/ pageindex / retrieval（BM25+向量）— 业务模块别在这造东西
    shared/             # 跨模块通用：exceptions / contracts
    modules/            # 业务模块：ingest / chat / auth，各自带 router+service+repository+models+schemas，互不 import
    main.py             # FastAPI 装配，注册各模块 router
  tests/{unit,integration}/
  pyproject.toml + uv.lock + Dockerfile（v0 留壳未启用）
product/                # 业务上下文：prd/（PRD）+ design/（mockup）
decisions/              # ADR（架构决策，只增不改）
docs/                   # 设计 / 实施方案（plans/，相对稳定的工程知识）
_archive/               # 旧开发过程文档冻结区（只读，不再更新，翻历史时看这里）
```

## 进度与协作（本项目流程 — 读这段）

仓库**只保留稳定知识**：本文件（规则）/ `product/prd`（业务）/ `decisions/ADR`（决策）/ `docs/`（设计方案）。
**进度和任务属于活数据，不进仓库**：

- 进度 / 任务 / 看板 → **飞书**
- 代码评审 / 变更记录 → **GitLab MR**（commit 和 MR 描述承担"改了什么、为什么改"，取代旧 `progress/changes/`）
- 不再维护 `progress/current.md` 这类手写全局状态文件（多人 / 多需求下必冲突、必过期）

> 本项目已**彻底移除 FuturX harness 框架**（`.harness/` 及其同步 CI），不再有 progress / changes / contracts / lessons 那套流程。仍沿用的轻约定：commit 带 task ID（`[T-XXX] type: ...`）、feature-first 模块隔离。

## 架构核心约束（读多文件才能搞懂的大图景）

- **feature-first 模块化**：每个业务模块在 `backend/app/modules/<name>/`，自带 router/service/repository/models/schemas
- **模块隔离**：业务模块之间默认禁止 `import` 对方，跨模块协作走 `app/shared/contracts/`。**例外（已知并接受）**：`chat` 为做"数据来源溯源"只读复用 `ingest` 的 `document_service`/`ocr_locate`/`page_locator`（见 chat/router.py 的 `/chat/node`）——v0 阶段不为此单独造 contract 层，后续需要再抽 Protocol
- **基座资源走依赖注入**：`Depends(get_db)` / `Depends(get_current_user)`，不要自己 `new`
- **LLM 调用统一走 LiteLLM Proxy（OpenAI 兼容）**：对话用 `ChatOpenAI`、PDF 解析用 `core/docparse`、embedding 用 `core/retrieval/embed`，模型名走 config，禁止直连厂商 SDK
- **检索方案：PageIndex 树 + BM25/向量混合检索（RRF）**：自上而下翻目录 + 自下而上检索；`search_nodes` 内部 BM25 + 向量加权 RRF 融合，向量是增强项、BM25 是底线（embedding 不可用则优雅退回纯 BM25）。embedding 走 LiteLLM Proxy。详见 `decisions/ADR-003` 与 `docs/plans/2026-06-24-hybrid-retrieval-design.md`
- **入库识别锁定纯 VLM**：保留 `<notsure>...</notsure>` 段落标记机制（不走 MinerU）
- **多格式上传**：docx/xlsx/pptx/txt 等先经 **Gotenberg**（独立容器，封装 LibreOffice，中文字体焊在 `docker/gotenberg/`）转 PDF，再走现有 PDF 管线；PDF 直接透传。Gotenberg 是**系统级服务、不是 Python 包**，靠 docker-compose 钉版本（本地 dev 跑 `docker compose up -d gotenberg`，`.env` 配 `GOTENBERG_URL`）。见 `app/modules/ingest/doc_convert.py`
- **渲染 DPI**：VLM 解析用 `pdf_render_dpi`（默认 500，高清助解析）；OCR 侧车（高亮框）用 `ocr_render_dpi`（默认 200，足够且快），两者解耦，均可 `.env` 覆盖
- **OCR 默认走 GPU**：`onnxruntime-gpu` + nvidia CUDA 库已写进 `pyproject`（含 CPU provider，无 GPU 自动回退）；`OCR_USE_GPU=false` 可强制 CPU。⚠️ 因 `rapidocr-onnxruntime` 传递依赖 CPU 版 onnxruntime，靠 `[tool.uv] override-dependencies` 覆盖掉——别删这条，否则 `uv sync` 会把 OCR 打回 CPU
- **新增依赖用 `uv add`**，禁手改 pyproject（例外：上面的 onnxruntime-gpu/override 需手写）

## 业务模块开发流程（写新模块照做）

1. 在 `backend/app/modules/<name>/` 建 router / service / repository / models / schemas
2. router 只做 HTTP 编排，业务逻辑落 service，数据访问落 repository
3. 先写 `tests/unit/` 覆盖 service，再写 `tests/integration/` 覆盖 router（走 TestClient + `dependency_overrides`）
4. 模块只暴露 router，跨模块需要的能力走 `app/shared/contracts/`
5. 在 `app/main.py` 注册 router

## 常用命令

```bash
# 后端
cd backend
uv sync                                              # 安装依赖
uv run uvicorn app.main:app --reload                 # 启动 dev server (8000)
uv run pytest -v                                     # 全量测试
uv run pytest tests/unit/test_hybrid_search.py -v    # 跑单个文件
uv run pytest tests/unit/test_x.py::test_y -v        # 跑单个 test
uv run ruff check . && uv run pyright                # lint + 类型
uv run python -m app.core.docparse <pdf>             # 单测 PDF→md 解析（需真 .env）
```

## 完成任务后

1. `uv run pytest` 全绿
2. `uv run ruff check .` + `uv run pyright` 零警告
3. 提交 GitLab MR；commit 带 task ID（`[T-XXX] feat/fix: ...`），变更说明写进 MR 描述

## 关键文件指引

| 路径 | 作用 |
|---|---|
| `product/prd/v0.md` | 业务上下文（v0.3） |
| `decisions/ADR-001-tech-stack.md` | 技术栈选型决策（云 VLM / 后 Python 前 Next.js / 数据规模） |
| `backend/app/modules/ingest/` | **业务模块完整范例**，写新模块照抄这个结构 |
| `backend/tests/integration/test_ingest_router.py` | 集成测试 fixture 范例（用 `dependency_overrides`） |
| `product/design/v0-mockup-v2.html` | 前端单页（玻璃态 Tailwind；main.py `GET /` 直接 serve；含 marked/pdf.js/mermaid/DOMPurify，均本地 vendor/） |

## 联系人

- Tech Lead：（待填）
- PM：（待填）
- 设计：（待填）

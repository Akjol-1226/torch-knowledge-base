# 代码地图 — 火炬电子知识库（torch_knowledge_base）

> 当前阶段：后端骨架（Sprint-0）完成。下一步 Sprint-1 填实入库流水线 + 前端骨架。

## 顶层入口
- 后端：`backend/app/main.py`
- 前端：（暂无）

## 后端（`backend/`）
- 入口：`backend/app/main.py`（FastAPI + lifespan + 路由挂载）
- 配置：`backend/app/core/config.py`（pydantic-settings + .env）
- 数据库：`backend/app/core/db.py`（SQLModel async engine + sessionmaker + get_db DI）
- 日志：`backend/app/core/logging.py`（structlog，dev console / prod json）
- VLM：`backend/app/core/vlm/`
  - `client.py`：VLMClient（基于 openai SDK 调 LiteLLM Proxy）
  - `schemas.py`：VLMResponse / NotsureSpan
  - `parser.py`：`<notsure>` 标记解析（栈式扫描，支持嵌套取最外层）
  - `smoke_test.py`：手动 smoke 脚本
- 业务模块（feature-first）：
  - `backend/app/modules/ingest/`：models / repository / service / router / graph（langgraph 占位）
  - `backend/app/modules/chat/`：router（占位 echo）
  - `backend/app/modules/auth/`：dependencies（CurrentUser 占位）
- 测试：
  - `backend/tests/conftest.py`：db_engine + db_session fixture（in-memory SQLite）
  - `backend/tests/unit/`：单测
  - `backend/tests/integration/`：集成测试（ASGI transport）

## docs/
- 设计：`docs/plans/2026-05-26-backend-skeleton-design.md`
- 实施 plan：`docs/plans/2026-05-26-backend-skeleton-plan.md`
- 路线图：`docs/roadmap.md`

## product/
- PRD：`product/prd/v0.md`（v0.3）
- 设计稿：`product/design/v0-mockup.html`（1587 行 Tailwind 玻璃态）

## decisions/
- ADR-001 技术栈选型：`decisions/ADR-001-tech-stack.md`
- ADR-002 VLM 厂商选型：（待写，PoC 完成后）
- ADR-003 BM25 后端：（待写）

## 关键约束 / 陷阱
- 详见 `progress/lessons.md`（已有 L-001 ~ L-004）
- AGENTS.md 业务模块开发约定 R-1 ~ R-10
- harness 5 条铁律：`.harness/AGENTS.base.md`

---

> **下次更新触发条件**：第一个业务 PR merge 后或目录结构稳定时手动刷新

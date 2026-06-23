# 当前状态 - 2026-05-26

## 项目阶段
**Sprint-0 后端骨架完成（T-006 ~ T-019）— review 通过，可下发业务模块开发**

## 后端骨架完成项
- `backend/` 目录起步，pyproject + uv + ruff + pyright + pytest 工具链全配齐
- 24 个 test 全绿（unit + integration），ruff 0 警告，pyright 0 错误
- 4 个业务 endpoint 可用：`/health`, `/ingest/upload`, `/ingest/tasks`, `/chat/echo`
- VLMClient（LiteLLM Proxy + OpenAI 兼容协议）、parser、smoke_test 脚本就绪
- langgraph 占位状态图（4 节点：load_pdf → extract_pages → build_pageindex → persist）
- SQLModel 模型：Document / Page / IngestTask
- docker-compose + Dockerfile 留壳（v0 未启用）

## 技术栈速览（v0 锁定）
- 业务：火炬电子（Torch Electronics）研发工程师的工艺/检验文件知识库问答系统
- 数据源：共享盘/NAS 的 PDF/Word/PPT（扫描件占比待客户对齐）
- 检索方案：PageIndex 风格树形检索（自上而下翻目录 + 自下而上 BM25），**不走向量检索**
- 入库识别：**纯 VLM 云模型路径**（v0 用云，v1+ 评估本地化；保留 `VLMClient` 接口）
- Agent 编排：langchain / langgraph
- **后端**：Python 3.11+ / FastAPI / langchain / langgraph / pydantic v2
- **前端**：Next.js 14 (App Router) / TypeScript / Tailwind / shadcn/ui
- **协议**：REST + SSE，OpenAPI → TS client 自动生成
- 数据库：v0 用 SQLite FTS5 或 Postgres tsvector（ADR-003 收口）
- 队列：FastAPI BackgroundTasks / asyncio queue（v0 不上 Celery）
- 部署路径：先云上快速验证 → 后期客户侧本地化部署（含 VLM 本地化）

## v0 数据规模
20 文档 × 40-130 页 ≈ 800-2600 页；VLM 全量入库估 $10-25；单机够用

## 进行中的任务
（无 — Sprint-0 后端骨架完成；下一步等团队 review 后选定 Sprint-1 入库流水线 / 前端骨架）

## 最近完成（最多 5 个）
- 2026-05-26 T-019 后端骨架 review 修复（P0/P1 blocker 4 项，29 test 全绿）
- 2026-05-26 T-006~T-018 后端骨架（B 档）完成（13 个 task，24 个 test 全绿）
- 2026-05-26 T-005 技术栈三选定型，ADR-001 落档（云 VLM / 后 Python 前 Next.js / 20 文档规模）
- 2026-05-26 T-004 v0 PRD 草案完成（v0.2 → v0.3，按 3+1 模板组织，9 节 + 6 附录）
- 2026-05-26 完成 v0 PRD brainstorming（产品三大块骨架）
- 2026-05-26 接入 FuturX Coding Harness v1.6 + 重置 progress

## 下一步建议
1. 团队 review 后端骨架（重点：feature-first 模块结构、VLMClient 抽象层、langgraph 占位节点设计）
2. 抽样 PoC：拿 5 份代表性文档跑端到端 VLM 入库，收口 ADR-002（VLM 厂商选型）
3. mockup v0-mockup.html（1587 行 Tailwind 玻璃态）迁移到 Next.js 组件骨架
4. 实际入库流水线：填实 langgraph 4 个占位节点（PDF 拆页 + VLM 抽取 + PageIndex 构建 + DB 持久化）
5. 与客户对齐：① 验收阈值（PRD 1.2 节四项指标）② 扫描件 vs 数字版比例

## 已知问题 / 待跟进
- 业务场景已明：研发工程师查工艺文件/检验标准
- 风险 R-001：PageIndex 上层 summary 与 leaf 修改的一致性（详见 PRD 风险章节）
- 风险 R-002：纯 VLM 处理复杂表格的结构性失败（人工抽检 SOP 兜底）
- 风险 R-003：原始 tool call/response 展示在客户高层演示时显得粗糙
- 待决 ADR-002：具体云 VLM 厂商 — 需 PoC 数据支撑
- 待决 ADR-003：BM25 后端（SQLite FTS5 vs Postgres tsvector）
- 演进方向：v0 全局管理员够用；后期文档量大或业务多元化时升级到「库级管理员」

## 关键链接
- PRD：`product/prd/v0.md`（v0.3）
- ADR：`decisions/ADR-001-tech-stack.md`
- 设计：`docs/plans/2026-05-26-backend-skeleton-design.md`
- 实施 plan：`docs/plans/2026-05-26-backend-skeleton-plan.md`
- 后端入口：`backend/app/main.py` / `backend/README.md`
- mockup：`product/design/v0-mockup.html`（玻璃态草稿，1587 行 Tailwind）
- 飞书 / GitLab：（待补）

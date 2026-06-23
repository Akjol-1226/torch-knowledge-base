# 任务地图（v0）

> PRD → Sprint × 业务模块 × 角色 的三维拆解。任务地图是 PRD 和 progress/changes 之间的桥梁层。
> 任务卡 spec 是**可选**的：基座 + 团队内部任务直接看本表 + 关联文档；外包 / 陌生人 onboard 时再单独为该 task 写 spec.md。

---

## Sprint-0 基座搭建（开发启动前，~3-4 天，Tech Lead 主导）

| TaskID | 名称 | 角色 | 估时 | 依赖 | 状态 | 实施参考 |
|---|---|---|---|---|---|---|
| T-006 | 后端骨架（FastAPI + SQLModel + LiteLLM + langgraph 占位） | 后端 | 1 天 | — | pending | `docs/plans/2026-05-26-backend-skeleton-plan.md` |
| T-007 | 前端骨架（Next.js + Tailwind + 迁移 mockup） | 前端 | 1.5 天 | — | pending | mockup: `product/design/v0-mockup.html` |
| T-008 | VLM PoC（5 份样本跑端到端，验证 R-002，收口 ADR-002） | 算法/后端 | 2 天 | T-006 | pending | — |
| T-009 | DevOps（docker-compose / CI / OpenAPI → TS client） | 全栈 | 1 天 | T-006, T-007 | pending | — |

**Sprint-0 出口条件**：
- 后端 `uv run uvicorn app.main:app` 跑通，`/health` 200
- 前端 `pnpm dev` 跑通，能加载 mockup 同款首页
- VLM PoC 报告产出（5 份样本的 raw_text + notsure_count + 表格质量评估）
- ADR-002（VLM 厂商）落档

---

## Sprint-1 入库模块（~2 周）

| TaskID | 名称 | 角色 | 估时 | 依赖 | 状态 |
|---|---|---|---|---|---|
| T-010 | PDF 拆页 + 文件存储 | 后端 | 1 天 | T-006 | pending |
| T-011 | VLM 抽取流水线（替换占位节点） | 后端 | 2 天 | T-008 | pending |
| T-012 | PageIndex 树构建 + 持久化 | 后端 | 3 天 | T-011 | pending |
| T-013 | 任务队列 + 状态机 | 后端 | 1 天 | T-010 | pending |
| T-014 | Review 流程（API） | 后端 | 2 天 | T-012 | pending |
| T-015 | 上传页面 + 任务队列页面（前端） | 前端 | 2 天 | T-007, T-013 | pending |
| T-016 | Review 列表 + Review 详情页（前端） | 前端 | 3 天 | T-007, T-014 | pending |

**关联 PRD**：附录 A.1 入库模块

---

## Sprint-2 检索 + 聊天（~2 周）

| TaskID | 名称 | 角色 | 估时 | 依赖 | 状态 |
|---|---|---|---|---|---|
| T-017 | 自上而下树形检索 | 后端 | 2 天 | T-012 | pending |
| T-018 | 自下而上 BM25（SQLite FTS5 / Postgres tsvector，待 ADR-003） | 后端 | 1 天 | T-012 | pending |
| T-019 | 聊天 agent（langgraph） | 后端 | 3 天 | T-017, T-018 | pending |
| T-020 | SSE 流式输出 | 后端 | 1 天 | T-019 | pending |
| T-021 | 聊天主页面 + tool call 折叠 + 引用跳转（前端） | 前端 | 3 天 | T-007, T-020 | pending |

**关联 PRD**：附录 A.2 聊天模块

---

## Sprint-3 权限（~1 周）

| TaskID | 名称 | 角色 | 估时 | 依赖 | 状态 |
|---|---|---|---|---|---|
| T-022 | 用户管理 API + 鉴权中间件 | 后端 | 2 天 | T-006 | pending |
| T-023 | 业务角色动态化 + RBAC | 后端 | 2 天 | T-022 | pending |
| T-024 | 用户管理 + 角色管理页面（前端） | 前端 | 2 天 | T-007, T-023 | pending |

**关联 PRD**：附录 A.3 权限模块

---

## 横切（贯穿全程）

| TaskID | 名称 | 角色 | 触发 |
|---|---|---|---|
| T-025 | 监控 / 错误处理 / 日志规范 | 全栈 | Sprint-1 第一个 bug 后启动 |
| T-026 | E2E 测试 | 全栈 | Sprint-2 完成后 |
| T-027 | 部署文档 + 客户侧本地化指南 | 全栈 | v0 验收前 |

---

## 工作量与时间估算

| 角色 | 任务数 | 估时 |
|---|---|---|
| 后端 | 13 | ~22 天 |
| 前端 | 4 | ~10 天 |
| 算法/全栈 | 6 | ~10 天 |
| **总** | **20** | **~42 人天** |

按 2-4 人团队、并行度 60-70% → **3-5 周可交付 v0**。

---

## 依赖图（关键路径）

```
Sprint-0:
  T-006 ─┬─ T-008 ─── (ADR-002)
         └─ T-009
  T-007 ──┘

Sprint-1（关键路径）:
  T-006 → T-010 → T-013 → T-015（前端任务队列页）
  T-008 → T-011 → T-012 → T-014 → T-016（前端 Review）

Sprint-2（关键路径）:
  T-012 → T-017 ┐
                ├→ T-019 → T-020 → T-021
  T-012 → T-018 ┘

Sprint-3:
  T-006 → T-022 → T-023 → T-024
```

**v0 上线的关键路径**：T-006 → T-008 → T-011 → T-012 → T-019 → T-020 → T-021

---

## 何时为某个 task 单独写 spec.md

满足任一条件就单独写 `progress/tasks/T-XXX-<name>/spec.md`（用 `progress/tasks/T-000-example-task/spec.md` 作模板）：

1. 任务要外包给陌生开发者 / 团队外协作者
2. 任务跨多个 Sprint（如 T-012 PageIndex 树）
3. 任务涉及多人协作（多个 sub-task 分给不同人）
4. 任务的验收标准比表格里一两行能写清楚的复杂得多

**否则不要写 spec**。本表 + 关联的 PRD / 实施参考文档已经够了。

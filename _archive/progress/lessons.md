# 踩坑日志 — 火炬电子知识库

> 凡是"业界推荐但其实错的"方案，记录在此。AI 读完后**禁止再用**。
>
> 格式：`L-XXX — 一句话标题` + 场景 / 错误方案 / 为什么会默认推荐 / 真实问题 / 正确方案 / 加入日期。

## L-001 — 写 PRD 不要自造结构，先按 harness 3+1 模板

- **场景**：v0 PRD brainstorm 收束后起草 product/prd/v0.md
- **错误方案**：找不到 `templates/pm-input-template.md` 文件后自己造了一个"传统咨询风格"的 10 节结构（业务背景 / 用户 / 场景 / 功能 / IA / 范围 / 非功能 / 风险 / 未决）
- **为什么 AI 会默认推荐**：传统 PRD 模板（PMP / 麦肯锡风格）的肌肉记忆，章节越多越显得"专业"
- **真实问题**：
  1. 违反 harness 的**轻量化原则**（v1.4 后明确的核心精神，见 ADR-008）
  2. ADR-009 已经定义了"PM 输入 3+1 模板"作为 PRD 标准（① 核心用户故事+主流程 / ② 业务边界 / ③ 验收标准 / ④ 设计规范），找不到模板文件 ≠ 模板不存在
  3. 自造结构让 PRD 在跨项目间漂移，违反 harness "统一约束层" 的目的
- **正确方案**：写 PRD 前**必读**：
  1. `.harness/AGENTS.base.md` 里关于 ADR-009 的引用
  2. `progress/tasks/T-000-example-task/spec.md`（task 级 spec 模板，与 PRD 精神同源）
  3. **找不到具体模板时主动询问用户**，不要默认自造
- **加入**：2026-05-26 by Claude（写 v0.1 PRD 后被用户拦下重构成 v0.2）

## L-002 — sqlalchemy 2.0 async + sqlmodel 必须显式装 greenlet 和 aiosqlite

- **场景**：T-009 搭 `core/db.py`，用 `sqlite+aiosqlite` driver + `create_async_engine`
- **错误方案**：只在 pyproject 列 `sqlmodel`，没列 `greenlet`、`aiosqlite`
- **为什么 AI 会默认推荐**：以为这两个是 sqlalchemy/sqlmodel 的 transitive dep，会自动装
- **真实问题**：
  1. `aiosqlite` 必须显式装，sqlalchemy 不会带（driver 是按 URL scheme 动态 import 的）
  2. `greenlet` 是 sqlalchemy async 内部 await/run_sync 桥接必需，sqlalchemy 在 mac arm64 上没把它列成强依赖
  3. 报错信息分别是 `ModuleNotFoundError: No module named 'aiosqlite'` 和 `ValueError: the greenlet library is required to use this function`，但都发生在 runtime，不是 install 时
- **正确方案**：pyproject 里同时显式列：
  - `aiosqlite>=0.20.0`
  - `greenlet>=3.0.0`
  - `sqlmodel>=0.0.22`
- **加入**：2026-05-26 by Claude（T-009 启动测试时撞上）

## L-003 — sqlmodel AsyncSession 测试 fixture 必须设 `expire_on_commit=False`

- **场景**：T-012 写 ingest models 测试，commit 一个对象后再做新对象 add + commit，期间访问第一个对象的属性
- **错误方案**：用默认 `AsyncSession(engine)` 不传 `expire_on_commit`
- **为什么 AI 会默认推荐**：默认值是 True，符合「commit 后所有 instance state 标 expire 等下次 query 重读」的 sync ORM 直觉
- **真实问题**：async session expire 后访问属性会触发隐式 lazy load = 同步 IO，但当前在 greenlet 之外 → 抛 `MissingGreenlet: greenlet_spawn has not been called`。报错信息看着像 driver/线程问题，其实是 expire 机制 + async 不兼容
- **正确方案**：所有 `AsyncSession(...)` 构造时显式 `expire_on_commit=False`（fastapi-users / 官方 async 教程的默认推荐）
- **加入**：2026-05-26 by Claude（T-012 测试时撞上）

## L-005 — 写 AGENTS.md 引用的 path 必须 verify 真实存在

- **场景**：T-019 review 后端骨架时发现 AGENTS.md R-2 写"跨模块走 `app.shared.contracts`"、R-6 写"异常用 `app.shared.exceptions`"，但 `app/shared/` 目录根本不存在
- **错误方案**：Tech Lead 写 AGENTS.md 约定时**只想 path 应该长什么样**，不去实际验证 path 存不存在
- **为什么 AI 会默认推荐**：写规范时是 top-down 设计的状态（描述理想），与 bottom-up 实施（确保存在）切换不流畅
- **真实问题**：业务开发者按 AGENTS.md 写 `from app.shared.exceptions import NotFoundError` 会立刻 ImportError，且找不到原因（约定看起来很对）。这是把陷阱直接下发给团队
- **正确方案**：写 / 更新 AGENTS.md 的硬规则后，**必须**对每个引用的 path 跑一遍：
  1. `ls <path>` 真实存在
  2. `python -c "from <path> import <symbol>"` 可 import
  3. 不存在则立刻创建占位文件，且这件事写进 changes
- **加入**：2026-05-26 by Claude（T-019 review 时撞上）

## L-004 — SQLModel `metadata.create_all` 之前必须显式 import 所有 model 模块

- **场景**：T-015 integration test 用 in-memory engine + `SQLModel.metadata.create_all` 建表，再调 `/ingest/upload`
- **错误方案**：以为只要项目里写了 `class Document(SQLModel, table=True)`，metadata 就自动有定义
- **为什么 AI 会默认推荐**：SQLAlchemy ORM declarative + 装饰器扫描的心智模型
- **真实问题**：`SQLModel.metadata` 只收集**已被 import** 的 model class。conftest 在 fixture 里 `from sqlmodel import SQLModel`，但 ingest/models.py 没被 import 过，class 定义没执行，metadata 为空 → `create_all` 不建任何表 → 测试请求时 `no such table: document`
- **正确方案**：conftest 顶部显式 `from app.modules.ingest import models as _ingest_models  # noqa: F401` 强制注册。每加一个 model 模块都要加一行
- **加入**：2026-05-26 by Claude（T-015 integration test 撞上）

# ADR-001 — v0 技术栈选型

| 项 | 内容 |
|---|---|
| 日期 | 2026-05-26 |
| 状态 | Accepted |
| 决策者 | 主开发（待补名） |
| 关联 | PRD `product/prd/v0.md` 附录 D（Q-001/Q-002/Q-003） |
| 取代 | — |

---

## 背景

火炬电子知识库 v0 在 PRD 完成时遗留三个未决问题：

- **Q-001** VLM 选型（云 vs 本地）
- **Q-002** 前后端语言栈
- **Q-003** 数据规模

三者都是开发前必须收口的事项，且互相耦合（数据规模 → VLM 单次入库成本 → 是否能容忍云模型；语言栈 → langchain 生态 → 后端落点）。本 ADR 一次性收口。

---

## 决策

### D-1：VLM 走云模型路径（v0），保留本地化迁移接口

**选定方案**：**v0 用云 VLM，v1+ 评估本地化迁移**。

- v0 阶段：云 VLM 直接调 API，快速验证产品形态
- 选型时优先考虑「同厂商有本地化可迁移开源版本」的模型（例如 Qwen-VL-Max 云版 ↔ Qwen2-VL 开源版），降低未来切换的 prompt / 输出格式差异
- 入库 pipeline 抽象一层 `VLMClient` 接口，云/本地实现可热替换

**不选**：v0 直接本地部署。

**理由**：

| 维度 | 云 | 本地 |
|---|---|---|
| 起步速度 | 当天可用 | 需采购/部署 GPU，数周起 |
| v0 数据规模成本 | $10-25 / 全量入库（见 D-3） | GPU 摊销远超 |
| 准确率（中文 + 复杂表格） | 商用 SOTA | 开源版本通常落后 6-12 月 |
| 客户侧本地化（v2+） | 不支持 | 必选 |

v0 的产品验证目标 >> 部署成本节省，先把"产品做对"再考虑"部署做便宜"。

**风险衔接**：与 PRD R-002（纯 VLM 在复杂表格上结构性失败）独立。云模型不能消除 R-002，依旧靠人工抽检 SOP 兜底。

---

### D-2：后端 Python（FastAPI + langchain/langgraph），前端 Next.js（TS + Tailwind + shadcn/ui）

**选定方案**：**双语言栈**。

- **后端**：Python 3.11+ / FastAPI / langchain / langgraph / pydantic v2
- **前端**：Next.js 14 (App Router) / TypeScript / Tailwind CSS / shadcn/ui
- **协议**：REST + SSE（聊天流式）；后端 OpenAPI schema → 前端自动生成 TS client

**不选**：

| 候选 | 否决理由 |
|---|---|
| A. 全栈 TS（Next.js + LangChain.js） | LangChain.js 生态落后 Python 版本 6-12 月，缺关键组件（部分 retriever / evaluation 工具） |
| C. FastAPI + Jinja2 + HTMX 单 Python 栈 | mockup 是玻璃态精致风，HTMX 实现成本高于 React；流式聊天 UI 困难 |
| D. Streamlit / Gradio 纯 Python | 视觉精度与"客户高层演示"门槛差距过大；多页面 + 复杂交互（SideBar、入库流程、tool call 折叠）超出 Streamlit 舒适区 |
| E. Reflex（Python→React 编译） | 社区小、生态弱、bug 难排查；与 mockup（Tailwind）适配差 |

**理由**（按权重排序）：

1. **mockup 已经是 Tailwind**（`product/design/v0-mockup.html`，1587 行玻璃态原型）。换其他栈等于扔掉这份资产。
2. **sibling 项目 `knowledge_base` 是 Next.js**，组件与工具可复用，团队心智一致。
3. **聊天流式 / tool call 折叠 / token 流渲染**是 React 生态舒适区（Vercel AI SDK、shadcn chat 模板可省 1-2 周）。
4. **langchain 生态在 Python 远胜 TS**。RAG/retriever/evaluation/tracing 关键组件在 Python 端更成熟。

**接受的代价**：

- 双语言栈运维复杂度 ↑：v0 单机 docker-compose 可控；v2+ 客户内网部署再考虑打包简化
- 类型边界跨语言：用 OpenAPI codegen 自动同步，避免手动对齐 schema
- 团队 CI 需双工具链：mypy + ruff（Python）+ tsc + eslint（TS）

---

### D-3：v0 数据规模锁定 — 20 文档 × 40-130 页

**已知规模**：

| 项 | 值 |
|---|---|
| 文档数量（v0 测试） | 20 |
| 单文档页数 | 40-130 页 |
| 总页数 | 800-2600 页 |
| 扫描件 vs 数字版比例 | 待客户对齐（影响 VLM 工作量与 OCR 兜底策略） |

**对设计的影响**：

| 维度 | 结论 |
|---|---|
| VLM 入库成本 | 按云模型 $0.005-0.01/页估，**全量入库 $10-25**，可试错 |
| 任务队列 | 无需 Celery / Redis 分布式队列，**FastAPI BackgroundTasks 或单进程 asyncio queue** 足够 |
| 数据库 | v0 不需要专门的检索引擎；BM25 走 SQLite FTS5 或 Postgres `tsvector`；PageIndex 树存关系表即可 |
| 并发 | 入库串行 / 单并发即可（v0 用户量 < 10） |
| PageIndex 树深 | 单文档 130 页 → 估 4-5 层（页/段 → 章节 → 文档 → 库），可控 |

**v1+ 扩展预留**：

- 入库流水线接口可平移到 Celery（任务定义不变）
- BM25 后端可换成 ElasticSearch / Meilisearch
- PageIndex 树的存储抽象，不要硬编码 SQLite 结构

---

## 决策依据简表

| 问题 | 决策 | 关键依据 |
|---|---|---|
| Q-001 | 云 VLM，保留迁移接口 | 起步速度 + v0 数据量级成本可控 |
| Q-002 | 前端 Next.js + 后端 FastAPI/Python | mockup 资产 + langchain 生态 + 招聘 + 演示精度 |
| Q-003 | 20 文档 × 40-130 页 ≈ 2600 页 | 客户给出的 v0 测试规模 |

---

## 后果（Consequences）

### 正面

- 三个未决问题全部收口，可进入开发
- 前端 mockup 可零损耗迁移到 React 组件
- 后端可立刻动手搭 langchain pipeline
- VLM 调用可立刻起 PoC（不必等本地 GPU 采购）

### 负面（已接受）

- 双语言运维：v2+ 客户本地化部署需做容器打包优化
- 云 VLM 成本随业务量上涨；切本地化是 v1+ 议题
- 招聘需双工具链人才（或前后端分工）

### 衔接的后续动作（不在本 ADR 范围）

- ADR-002（待写）：具体 VLM 厂商选型（Claude vs Doubao vs Qwen 云版）
- ADR-003（待写）：检索引擎选型（SQLite FTS5 vs Postgres tsvector）
- 客户对齐：扫描件 vs 数字版比例（影响 OCR 兜底策略）
- 抽样 PoC：拿 5 份代表性文档跑端到端 VLM 入库，验证 `<notsure>` 命中率与表格识别质量

---

## 关联

- PRD `product/prd/v0.md` 附录 D：本 ADR 取代其中 Q-001/Q-002/Q-003 的"待决"状态
- mockup `product/design/v0-mockup.html`：前端选型的关键资产

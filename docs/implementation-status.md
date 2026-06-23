# v0 实现状态（对照 PRD §3.4 功能完整性 checklist）

> **用途**：记录代码**实际实现到哪一步**，避免"界面看起来像做了、其实后端没做"的误解。
> **权威进度/排期看飞书**；本表是**代码能力快照**——每完成或改动一项功能，同步更新这里。
> **最后更新**：2026-06-22

## 总览

| 模块 | 状态 |
|---|---|
| 核心问答（PageIndex 检索 + 流式答 + 引用 + 不兜底） | 已做 |
| PDF 入库识别（VLM 逐页 → Markdown，段落级 `<notsure>`） | 已做 |
| notsure 人工待审闸门（确认/修正后入库建树） | 已做 |
| 多知识库 + 检索范围隔离 | 已做 |
| 异步任务队列 + 状态机 | 已做 |
| 会话管理（会话列表 / 历史持久化） | 已做 |
| 文档查看 / 删除 | 已做 |
| 权限 / 用户管理 / 角色管理 / 登录 | 未做 |
| Word / PPT 入库 | 未做 |

## 入库（PRD §3.4）

| PRD 要求 | 状态 | 说明 |
|---|---|---|
| PDF 上传 | 已做 | `/ingest/upload-pdf`，单文件；上传保留原 PDF（`data/pdf/`） |
| Word / PPT 上传 | 未做 | 计划走"先转 PDF 再喂"，细节未定 |
| 拖拽**批量**上传、同批绑定库 | 未做 | 当前单文件 |
| 单文件 ≤ 200MB 校验 | 未做 | 无大小校验 |
| 段落级 `<notsure>` 标记 | 已做 | DocVisionMD 原生保留 |
| 任务**异步**执行 + 并发配置 | 已做 | BackgroundTasks + asyncio.Semaphore；并发默认 3（env `INGEST_CONCURRENCY`）。上传立即返回 task_id |
| 失败自动重试 3 次（指数退避） | 已做 | 代码完整（退避 30s/2min/10min）；happy path 实测，重试路径未实触发 |
| 任务状态机（排队→处理→待review→已入库→失败） | 已做 | queued→processing→needs_review/done→failed；文件存储 `data/tasks/` |
| Review：右识别可编辑 + 三动作 | 部分 | 右识别可编辑、确认即入库；左原图栏、独立三按钮未做 |
| leaf 改动留痕（who/when/before/after） | 未做 | 无登录无 who |
| leaf 改后异步重生成祖先 summary | 未做 | |

## 聊天（PRD §3.4）

| PRD 要求 | 状态 | 说明 |
|---|---|---|
| PageIndex 双向检索 | 已做 | 5 个只读工具 + BM25 |
| 多轮会话 + context 累积 | 已做 | 前端传 history |
| 会话列表（永久保留/命名/收藏/删除） | 已做 | 文件存储 `data/conversations/`；列表/加载历史/新建/删除已做；命名、收藏未做 |
| 中文输入法（IME）回车不误发 | 已做 | compositionstart/end + isComposing + keyCode229 三重判断 |
| 数字徽章 `[1][2][3]` 引用 | 部分 | 答案文本含 `[n]`，未渲染成可点徽章 |
| 右侧引用面板始终可见 | 已做 | |
| hover/click 双向联动（徽章 ↔ 卡片） | 未做 | 动态卡片未接联动 |
| tool call 原始 call/response 可展开 | 已做 | ai-steps 展开显示原始 call(name+args) + response(截断 2000 字) |
| 引用卡片 Markdown / 原 PDF 切换 | 未做 | 聊天引用卡片仍无；但知识库「查看文档」已支持 md / PDF 切换 |
| 引用卡片显示来自哪个知识库 | 部分 | sources 文本含来源 |
| 跨知识库按权限并集自动检索 | 部分 | 做了库过滤；"按用户权限并集"依赖权限模块 |
| 无答案不兜底 | 已做 | prompt 规则约束 |

## 文档管理（PRD 主流程"一键看原文" + 维护）

| 功能 | 状态 | 说明 |
|---|---|---|
| 查看文档（解析后 md 全文 + 原 PDF 切换） | 已做 | marked 渲染 md（含 HTML 表格）；新上传文档可看原 PDF，历史从 md 入库的只有 md |
| 删除文档（删 md + PDF → 重建树/索引/目录） | 已做 | store 经 catalog mtime 自动重载（删了即搜不到） |

## 权限（PRD §3.4）—— 整块未做

| PRD 要求 | 状态 |
|---|---|
| 业务角色动态创建/编辑/删除 | 未做 |
| 业务角色关联多知识库（多对多） | 未做 |
| 功能角色（管理员/普通用户，内置） | 未做 |
| 用户管理（新建/编辑/禁用） | 未做 |
| 关键写操作留痕 | 未做 |
| 登录鉴权 | 未做 |

## IA（PRD §3.4）

| PRD 要求 | 状态 |
|---|---|
| 入口：聊天 / 知识库管理 / 系统 | 部分（聊天、知识库管理有；系统未做） |
| 知识库管理含 上传/任务队列/待review/知识库 | 已做（任务队列已补回） |
| 系统含 用户管理/角色管理 | 未做 |
| 普通用户仅见聊天、管理员见全部 | 未做（无登录） |

## 关键架构事实（给后续开发）

- **入库异步队列**：`upload-pdf` 建任务(queued)→BackgroundTasks 后台 worker（asyncio.Semaphore 限并发）→线程池跑阻塞的 VLM 解析/建树→done/needs_review/failed。任务态文件存储 `data/tasks/`。**进程内队列**：server 重启会丢在跑的 queued/processing 任务（不自动恢复），v0 单机可接受；v1 要持久队列需引入持久 worker。
- **存储全文件化**（`data/`）：树/索引/目录/Markdown、会话(`conversations/`)、任务(`tasks/`)、原 PDF(`pdf/`)。会话/任务/查看都没用 DB。
- **检索 store 自动重载**：chat 端 `get_store()` 用 catalog 文件 mtime 作签名，入库/审核/删除重建后下次检索自动重载（修了过去"重建后需重启 server 才生效"的问题）。
- **遗留两套 ingest**：DB 骨架（`service.py`/`repository.py`/`models.py`，`/upload` + `upload_document` 占位空壳，仅集成测试用）vs 真实文件流程（`tree_service`/`docparse_service`/`task_service`/`task_worker`）。**真实入库走异步队列**，DB 骨架是死代码待清理。
- **无登录、无 user 概念**：所有"按用户/角色/who"的功能（权限、用户管理、改动留痕）都依赖先做登录 + 权限模块。
- **前端**：单文件 `product/design/v0-mockup-v2.html`，由 `main.py` 的 `GET /` 直接返回；已清除全部设计稿假数据，所有展示走真实后端 API；文档查看弹窗用 marked CDN 渲染 md。

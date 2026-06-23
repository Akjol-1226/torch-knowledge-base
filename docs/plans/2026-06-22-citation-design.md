# 引用来源改造设计：分组去重 + inline node 引用 + 关键词高亮

日期：2026-06-22
状态：已实现并端到端验证（2026-06-22）。详见文末「实现与验证」。

## 1. 背景与现状

聊天问答右侧的"数据来源"展示不准：同一文档被引用多次时出现多张卡片；正文里没有可点的引用锚点，用户无法从答案某句跳到它依据的原文。

现状链路（已核对代码）：

```
检索工具(treestore.search_nodes/read_node) 构造 cite={doc,section,lines}
  → sse.extract_cite 把 cite 拍平成字符串 "文档 · 章节 · 行X-Y"
  → sse._map_event 按整条字符串去重，累积进 state["sources"]: list[str]
  → answer 事件 {type,text,sources} 下发
  → 前端 renderRefs(sources) 按 " · " split 渲染卡片，徽章 1/2/3 仅为序号
```

两个根因：

1. **去重粒度错**：去重 key 是 `doc·section·lines` 整条字符串，所以同一文档的不同章节/行号被当成不同来源 → 文档 A 出 N 张卡。
2. **正文与来源没打通**：`prompt.py` 只让模型在答案末尾自由写一行"来源：…"，与右侧面板是两套互不关联的东西，没有锚点编号映射。`node` 的全局唯一 id（handle，如 `doc_3:0001`）和 `snippet` 在工具返回里存在，但**都没进 `cite`、没进 `sources`**。

关键事实：本系统**无页码概念**，定位粒度是"文档 · 章节标题 · 行号区间"（markdown 行号）。

## 2. 目标（用户三诉求）

1. 同一文档在右侧只出现一次（同文档的多个被引片段归到一张卡下）。
2. 高亮被引用的部分。
3. 正文 inline 引用：agent 输出时就带可点引用，点击后右侧展开/高亮对应 node。

## 3. 已确认决策

| 决策点 | 选定 | 理由 |
|---|---|---|
| inline 引用锚点机制 | **LLM 输出结构化标记，绑真实 node id** | 锚点指向工具真实返回的 node handle，不靠模型编号，幻觉率低 |
| 高亮粒度 | **章节(node)级 + 关键词高亮** | 模型零额外负担（只输出 node id），复用已有 `snippet`/关键词逻辑，最稳 |

被否决：模型自己编号 `[1][2]`（编号易错乱/漏标）；后端句子-node 自动对齐（项目不走向量检索，对齐易错且重）。

## 4. 总体方案（三步迭代）

```
第1步【独立·先做·低风险】 → 诉求(1)
  cite 补全字段 + sources 改为按文档分组、node 去重 + 前端分组渲染

第2步【新地基】 → 诉求(3)
  prompt 要求正文输出 [[cite:<node_id>]] + 前端解析成可点上标 + 点击联动右侧

第3步【精度】 → 诉求(2)
  右侧 node 卡可展开原文 + 命中关键词标黄；点击上标滚动+高亮对应 node
```

第1步与现状是平滑升级，可独立上线；第2、3步依赖第1步的结构化 sources。

## 5. 详细设计

### 5.1 数据契约（贯穿三步）

**(a) `cite` 字段扩展** —— 改 `backend/app/core/retrieval/treestore.py`

`read_node`（约 117 行）和 `search_nodes`（约 196 行）里构造 `cite` 的地方，补 4 个字段：

```python
cite = {
    "handle":   node_id,          # 新增：全局唯一锚点 "doc_3:0001"
    "doc_id":   n["doc_id"],       # 新增：分组用
    "doc_name": n["doc_name"],     # 原 doc
    "kb":       n["kb"],           # 新增
    "section":  n["title"],
    "lines":    n.get("lines", ""),
    "snippet":  make_snippet(n["text"], query),  # 新增（read_node 无 query，取 text 开头窗口或留空）
}
```

> 注意：`read_node` 没有 `query`，其 `snippet` 可填 `n["text"][:120]` 或留空，由前端展开时再处理；`search_nodes` 已有 `make_snippet(n["text"], query)`，直接复用。保留旧 `doc` 键与否按调用方兼容性定（建议改名 `doc→doc_name` 并全局搜引用）。

**(b) `sources` 结构** —— 改 `backend/app/modules/chat/sse.py`

从 `list[str]` 改为按 `doc_id` 分组、node 按 `handle` 去重：

```python
sources = [
  {
    "doc_id": "doc_3",
    "doc_name": "某工艺文件",
    "kb": "default",
    "nodes": [                       # 同文档多个被引片段，按 handle 去重
      {"handle": "doc_3:0001", "section": "5.3 回流焊", "lines": "120-180", "snippet": "...峰值温度245℃..."},
      {"handle": "doc_3:0007", "section": "6.1 检验",   "lines": "200-240", "snippet": "..."}
    ]
  }
]
```

改造点：
- `extract_cite`：不再拍平成字符串，返回结构化 `cite` dict 列表。
- `_new_state`：`seen` 改为按 `handle` 去重的 set；新增 `doc_index`（doc_id → sources 列表下标）便于分组追加。
- `_map_event`（约 61-64 行）：拿到 cite 后，若 `handle` 未见过 → 找到/新建该 doc 的分组 → 把 node 追加进 `nodes`。
- `_answer_event`：`sources` 输出上面结构。
- `_cite_str` 可保留给非流式 `/chat` 调试用，或删。

**(c) inline 标记语法**

正文标记：`[[cite:<handle>]]`，例如 `回流焊峰值温度 245℃[[cite:doc_3:0001]]`。
- 解析正则：`\[\[cite:([^\]]+)\]\]`
- 多 node 支撑同一句：`[[cite:doc_3:0001]][[cite:doc_5:0002]]`（连续两个标记，前端各渲染一个上标）。

**(d) 编号规则（正文徽章 ↔ 右侧卡一致）**

编号是 **node 级**，由前端统一分配：扫描 answer text 里 `[[cite:handle]]` 的**首次出现顺序**给每个 handle 编号 1,2,3…；右侧文档卡内每个 node 显示同一个编号。文档卡本身是分组容器，不单独编号。

### 5.2 第1步：分组去重（诉求 1）

- 后端：5.1(a) + 5.1(b)。
- 前端 `renderRefs`（约 696 行）重写：入参从 `list[str]` 变 `list[{doc_id,doc_name,kb,nodes[]}]`；外层渲染文档卡（标题=doc_name），卡内列出每个 node（section · 行号），node 旁放编号徽章。
- `refCount` 文案：`数据来源 · {文档数}`（或"N 篇 · M 处"）。

验收：同文档多次引用 → 右侧 1 张卡，卡内 N 个 node 条目。

### 5.3 第2步：inline 引用（诉求 3）

- `backend/app/modules/chat/prompt.py` 改【回答格式】段：
  - 要求：在每个有据的陈述句**句末**写 `[[cite:<id>]]`，`<id>` 原样填 `search_nodes`/`read_node` 返回的 `id` 字段，**禁止自己编号、禁止臆造 id**。
  - 给 few-shot 正例（句末带标记）和反例（写 `[1]`、写不存在的 id）。
  - 保留"找不到就直说"等原则不变。
- 前端 `renderMarkdown`（answer 阶段调用，约 837 行）：在 markdown 渲染后/前，把 `[[cite:handle]]` 替换成可点上标 `<sup class="cite-anchor" data-handle="...">{编号}</sup>`。
  - **校验**：handle 必须在 `ev.sources` 的某个 node 里才渲染成可点；不在 → 降级为去掉标记或灰色不可点（**绝不凭空造卡**）。
- 流式期间（chunk，约 832 行 `textContent`）：标记会以原文 `[[cite:...]]` 短暂出现。MVP 接受；可选增强见 5.5。

验收：正文出现可点上标，编号与右侧一致；点击有响应（见第3步联动）。

### 5.4 第3步：高亮与联动（诉求 2）

- 右侧 node 条目可展开：默认显示 `snippet`，展开显示更多上下文；命中关键词用 `<mark>` 标黄（复用 query 词；snippet 已是关键词窗口）。
- 点击正文上标 → 右侧滚动到对应文档卡 → 展开对应 node → 高亮（卡片描边 + node 原文关键词标黄）。
- 反向（点右侧 node 高亮正文所有引用它的上标）：可选增强。

## 6. 风险与兜底

| 风险 | 对策 |
|---|---|
| 模型标了不存在的 id | 前端按 sources 校验 handle，命中才可点，否则降级；绝不造卡 |
| 模型干脆不标 inline | prompt 强约束 + few-shot；若 answer 正文零标记，回退现状"右侧分组卡 + 末尾来源文本" |
| 流式标记被切断 | answer 事件带完整 text、整体重渲染，故解析放 answer 阶段即可规避；流式期间仅原文短暂可见 |
| `doc` 改名 `doc_name` 破坏旧引用 | 全局 grep `cite["doc"]` / `parts[0]` 等用法后统一改 |

## 7. 验收标准

- [ ] 同文档多片段引用 → 右侧仅 1 张卡，含多个 node 条目（诉求1）
- [ ] 正文出现可点 inline 上标，编号与右侧卡一致（诉求3）
- [ ] 点击上标 → 右侧定位+展开+关键词高亮对应 node（诉求2）
- [ ] 模型标错/漏标时不崩、不造假卡，能回退
- [ ] 后端单测覆盖 `extract_cite`/分组去重；集成测试覆盖 `/chat/stream` 的新 sources 结构

## 8. 不做（YAGNI）

- 不引入页码（系统本就无页码，行号已够）。
- 不做句/span 级精准高亮（已选 node 级；如后续需要再迭代，让模型带 quote）。
- 不做向量检索 / 句子-node 自动对齐。
- 不引入富文本编辑器，正文仍是 markdown 渲染 + 标记替换。

## 9. 实现与验证（2026-06-22）

改动文件：
- `backend/app/core/retrieval/treestore.py`：read_node / search_nodes 的 cite 补 handle/doc_id/snippet
- `backend/app/modules/chat/sse.py`：extract_cite 返回结构化 dict + 新增 `_add_cite` 按文档分组、按 handle 去重
- `backend/app/modules/chat/prompt.py`：【回答格式】改为硬性要求每个分点句末带 `[[cite:<id>]]`
- `product/design/v0-mockup-v2.html`：renderRefs 分组渲染 + citeIndex 编号 + replaceCiteMarkers 上标 + highlightTerms 关键词高亮 + focusCite 双向联动 + 流式期间隐藏未闭合标记

验证证据：
- 单测/集成：`uv run pytest` 294 passed, 2 skipped；`ruff` 通过；`pyright` 0 errors。新增 test_sse 分组去重/兜底用例、扩展 treestore/search_nodes cite 断言。
- 前端纯函数：node 跑 replaceCiteMarkers / highlightTerms / 流式过滤正则，确认未知 handle 降级、未闭合标记隐藏、XSS 安全。
- 端到端（真实 LLM + 真实入库数据，8012 起 server）：sources 正确按文档分组（NPD2426 一个文档 6 个 node 归一张卡，诉求1 生效）；强化 prompt 后同一问题 inline 标记数 1 → 8，每个分点都带标记。

待人工确认（未在浏览器实跑）：右侧卡片渲染、点击上标的滚动/高亮联动、关键词标黄的视觉效果——逻辑已就绪，需在浏览器点一次确认观感。

## 10. 迭代（2026-06-23）

真实使用暴露两个问题并修正（均为前端改动，后端 sources 仍为检索召回全量、不变）：

1. **来源语义修正**：右侧「数据来源」原先展示检索召回全集（模型翻过的所有 node），与正文 `[[cite:]]` 引用集对不上（出现"右侧 8 个、正文只引 1 个"的困惑）。改为只展示**正文实际引用的 node**——前端 `pickCitedSources` 按正文 `[[cite:handle]]` 过滤；正文零引用或全是臆造 id 时回退全量兜底。右侧与正文严格对应。
2. **消息级引用面包屑**：每条 AI 回答气泡下方新增引用面包屑 `renderMsgRefs`，列出本条回答引用的来源 chips（编号+文档名·章节），点击联动右侧，与右侧面板/正文上标共用同一 `citeIndex` 编号。

node 验证：过滤/回退/编号/面包屑组装逻辑均正确（含多文档部分引用、臆造 id 回退）。

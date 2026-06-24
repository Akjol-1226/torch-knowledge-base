# 混合检索设计：search_nodes 内置 BM25 + 向量（RRF 融合）

日期：2026-06-24
状态：设计待审

## 1. 背景与现状

当前检索是**纯 BM25**（`bm25s`），索引落 `data/indexes/`，节点级粒度。链路（已核对代码）：

```
入库 tree_service.ingest_dir
  → iter_nodes(doc_id, tree) 产出 node records[{node_id_full,title,summary,text}]
  → build_index(all_records).save(out/"indexes")              # core/retrieval/bm25_index.py
查询 TreeStore.search_nodes(query, top_k, kbs)                 # core/retrieval/treestore.py:174
  → self._bm25.search(query, top_k*N) 得 raw 命中
  → 按 kb 过滤 + 同名窗口折叠(section.span) → 返回 top_k
```

BM25 擅长**精确词**（型号/图号/工序号，配合 `domain_dict_auto.txt`），但对**概念性/换种说法**的查询召回弱（关键词对不上就漏）。引入向量做语义召回、与 BM25 混合，可补这条短板。

> 注：本设计推翻 `AGENTS.md`（即 CLAUDE.md）中"**不走向量检索**"的锁定决策，见 §9。
> （"不走向量"的锁实际在 AGENTS.md，不在 ADR-001；ADR-001 D-3 仅就检索引擎留了 ADR-003 的口子。）

## 2. 目标与范围

**只做混合检索这一层**，作为地基。用户反馈驱动的"检索学习"是**后续独立 spec**，建在这层之上（向量算"相似问题"正好喂给它），本次不做。

- 把 `search_nodes` 内部从纯 BM25 升级为 **BM25 + 向量的 RRF 融合**。
- **agent 工具面零变化**：仍只有 `search_nodes`，返回结构（`id/title/score/snippet/cite/path/...`）完全不变 → SSE / 前端 / 引用链路全部无感。
- 向量是**增强项**，BM25 是**底线**：向量不可用时优雅退回纯 BM25。

## 3. 已确认决策

| 决策点 | 选定 | 理由 |
|---|---|---|
| 暴露方式 | **单一混合工具**（融合在 `search_nodes` 内部） | 检索策略选择可用 RRF 确定性做对，不外包给 agent 临场判断；零新增工具/提示词 |
| 融合算法 | **加权 RRF**（倒数排名融合） | 只对进了 top-N 的那一路计分 → 弱向量"沉默"而非负贡献，不会拖累 BM25；无需归一化两套分数 |
| embedding 来源 | **LiteLLM Proxy `/embeddings`**（本地 onnxruntime 兜底，见 §6） | 复用团队统一入口；与现有 LLM 调用同一凭证 |
| embedding 输入 | **`title + summary + text` 顺序拼接后截断** | summary 是 LLM 摘要、语义核心；summary 在前 → 截断只丢正文尾部，长节点不整条失真 |
| 截断上限 | **取所用模型真实上限（config 配置）**，不写死 | Proxy 常见 embedding 模型上限 ~8K（OpenAI/bge-m3/通义 v3），512 是 BERT 本地模型的限制、不适用 |
| 长节点 chunking 多向量 | **不做（YAGNI）** | 91% 节点 ≤ 中位 250 token；长尾针尖问题 BM25 全文已兜住；评测确认漏召回再针对性加 |

被否决：加权**分数**融合（弱向量会直接拉低混合分，正是要避免的）；多工具让 agent 自选（模型选检索策略不稳、增 token/延迟）。

**实测语料分布（695 节点，建模依据）**：字符中位 360 / p90 1912 / max 26800；>512 字符占 43%、>2000 占 9%、>8000 仅 1 个。

## 4. 总体方案

```
索引侧（入库时，与 BM25 同批 records）
  all_records ──┬─► build_index(...).save(indexes/)          # BM25，不变
                └─► VectorIndex.build(records, embed).save(indexes/)   # 新增：embeddings.npy + 缓存

查询侧（TreeStore.search_nodes）
  query ──┬─► bm25.search(q, top_n)        → bm25_hits[(id,rank)]
          └─► embed(q) → vec.search(...)    → vec_hits[(id,rank,sim≥阈值)]
  rrf_fuse(bm25_hits, vec_hits, w_bm25, w_vec, k) → fused 有序 id 列表
  → 接回现有 kb 过滤 + 同名窗口折叠 → 返回 top_k（结构不变）
```

## 5. 详细设计

### 5.1 新增模块（`core/retrieval/`）

**(a) `embed.py` —— EmbeddingClient（Proxy 实现）**

```python
class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...   # 返回 (N, D) float32，已 L2 归一化

class ProxyEmbeddingClient:
    """走 LiteLLM Proxy 的 /embeddings（OpenAI 兼容），复用 litellm_base_url/api_key。"""
    # openai SDK：client.embeddings.create(model=settings.embedding_model, input=batch)
    # 批量分页（如每批 64 条）；L2 归一化以便用点积当余弦
```

- 接口化（Protocol）：本地 onnxruntime 兜底实现（§6）走同一接口，调用方无感。
- 归一化后，余弦相似度 = 点积，向量检索就是一次矩阵乘。

**(b) `vector_index.py` —— VectorIndex**

```python
class VectorIndex:
    # embeddings: np.ndarray (N, D) float32, L2 归一化；ids: list[str] 与行对齐
    @classmethod
    def build(cls, records, embed: EmbeddingClient) -> "VectorIndex":
        # text_i = trunc(title + "\n" + summary + "\n" + text, settings.embedding_max_chars)
        # content-hash 缓存：sha1(text_i) 命中旧 embeddings 则跳过，不重复调 API
        ...
    def save(self, dir_path):  # embeddings.npy + vec_meta.json(ids) + embed_cache.json(hash→行)
    @classmethod
    def load(cls, dir_path) -> "VectorIndex | None":  # 缺文件返回 None（触发纯 BM25 降级）
    def search(self, qvec, top_n, threshold) -> list[dict]:
        # sims = embeddings @ qvec；取 top_n；过滤 sim < threshold；返回 [{node_id_full, sim}]
```

- 规模小（~700 向量），内存 numpy 点积，**不引入 faiss/向量库**。
- `embed_cache.json` 按内容 hash 复用，避免每次全量重建狂调 Proxy。

**(c) `fusion.py` —— 加权 RRF（纯函数，易测）**

```python
def rrf_fuse(bm25_hits, vec_hits, *, w_bm25, w_vec, k) -> list[tuple[str, float]]:
    """bm25_hits/vec_hits: 各自按相关度降序的 node_id_full 列表。
    score(id) = w_bm25 * 1/(k + rank_bm25) + w_vec * 1/(k + rank_vec)
    缺席某一路 → 该路贡献 0（不是负分）。返回按融合分降序的 [(id, score)]。"""
```

默认 `w_bm25=0.6, w_vec=0.4, k=60`。

### 5.2 索引侧改动（`tree_service.ingest_dir`，约 75-77 行）

现有：
```python
if all_records:
    build_index(all_records).save(out / "indexes")
```
改为同批再建向量索引（失败不阻断入库，BM25 仍建成）：
```python
if all_records:
    build_index(all_records).save(out / "indexes")
    try:
        VectorIndex.build(all_records, get_embed_client()).save(out / "indexes")
    except Exception:
        log.exception("vector_index_build_failed")   # 降级：本次只有 BM25
```

### 5.3 查询侧改动（`TreeStore`）

- `_load`（约 41-43 行）：BM25 之后 `self._vec = VectorIndex.load(idx_dir)`（缺失则 `None`）。
- `search_nodes`（174 行）：在现有"取 raw 命中"处改为融合：

```python
def search_nodes(self, query, top_k=8, kbs=None):
    if self._bm25 is None:
        return {"error": "检索索引未构建或未加载（请先入库 ingest）"}
    bm25_ids = [h["node_id_full"] for h in self._bm25.search(query, top_k * (6 if kbs else 3))]
    vec_ids = []
    if self._hybrid_on() and self._vec is not None:
        try:
            qvec = get_embed_client().embed([query])[0]
            vec_ids = [d["node_id_full"] for d in
                       self._vec.search(qvec, settings.retrieval_top_n, settings.vec_sim_threshold)]
        except Exception:
            log.warning("query_embed_failed_fallback_bm25", exc_info=True)  # 退回纯 BM25
    fused = rrf_fuse(bm25_ids, vec_ids, w_bm25=..., w_vec=..., k=...) if vec_ids \
            else [(i, 0.0) for i in bm25_ids]
    # —— 以下完全沿用现有逻辑：遍历 fused 的 id，kb 过滤 + 同名窗口折叠，凑够 top_k ——
```

- 返回项里的 `score` 改为融合分（前端只展示/排序，无语义假设，兼容）。
- **kb 过滤、section 折叠、snippet、cite 全部不动** → 返回结构与现状逐字段一致。

### 5.4 配置（`Settings`，`core/config.py`）

```python
embedding_provider: str = "proxy"    # "proxy" | "local"，决定 get_embed_client() 选哪个实现
embedding_model: str = ""            # Proxy 路由名，如 "openai/text-embedding-3-small"（provider=proxy 时用）
embedding_max_chars: int = 6000      # 截断（字符近似，~8K token 留余量）
hybrid_enabled: bool = True          # 一键回退纯 BM25
retrieval_top_n: int = 50            # 向量召回候选数
rrf_k: int = 60
rrf_w_bm25: float = 0.6
rrf_w_vec: float = 0.4
vec_sim_threshold: float = 0.30      # 向量准入阈值：低于此的命中不参与融合
```

**生效判定（`_hybrid_on()`）**：`hybrid_enabled=True` 且能取到可用的 embedding client。
- `provider=proxy` 但 `embedding_model` 为空 / 探针失败 → 视为不可用 → 纯 BM25。
- `provider=local` → 用本地 onnxruntime 模型（见 §6），与 `embedding_model` 无关。

## 6. embedding 来源与兜底

- **主路：LiteLLM Proxy `/embeddings`**。**前置校验（实现第一步）**：对 Proxy 发一条 `embeddings.create` 探针，确认挂了 embedding 模型并拿到维度 D。
- **若 Proxy 未挂 embedding 模型** → 启用**本地 onnxruntime 兜底**（`onnxruntime` 已是依赖）：本地中文模型（如 `bge-small-zh-v1.5` ONNX，~100MB），实现同一 `EmbeddingClient` 接口，`get_embed_client()` 按 config 选择 provider。调用方/索引/融合均无感。
- 两条路产出的向量**不可混用**（模型不同向量空间不同）：索引与查询必须同一 provider；切换 provider 需重建向量索引（`embed_cache.json` 记录 provider+model 签名，签名变则全量重编码）。

## 7. 降级与风险

| 风险 | 对策 |
|---|---|
| Proxy embedding 查询时不可达 | catch → 退回纯 BM25，告警，不报错 |
| `embeddings.npy` 缺失（旧数据没重建） | `VectorIndex.load` 返回 None → 纯 BM25 |
| 弱/稀释向量（长节点）污染结果 | 用 RRF（缺席=0 贡献）+ `w_bm25>w_vec` + `vec_sim_threshold` 准入；弱向量自然沉默 |
| 向量召回塞"语义像但不对"的邻居 | 阈值 + 权重 + top_n 控制；评测调参 |
| 每次全量重建狂调 Proxy | 内容 hash 缓存，未变节点不重编码 |
| provider/模型切换导致向量空间错位 | 缓存记 provider+model 签名，签名变则全量重编码 |
| 中文技术文本 embedding 质量差 | 选中文友好模型；阈值/权重可把向量影响调低直至 0 |

## 8. 测试

- **单元**
  - `rrf_fuse`：给定两路 rank → 期望融合序；缺席一路 = 0 贡献；权重生效。
  - `VectorIndex.search`：构造已知向量，验证 top-N 与阈值过滤。
  - `embed` 客户端：mock Proxy，验证批量/归一化/签名。
  - 降级：`embed` 抛错 → `search_nodes` 返回 BM25 结果且**结构不变**。
  - hash 缓存：未变节点命中缓存、不重复编码。
- **集成**
  - 小语料同时建 BM25 + 向量，跑 `search_nodes`：验证融合命中 + 返回结构与纯 BM25 逐字段一致；`hybrid_enabled=False` 时与旧行为完全相同。

## 9. ADR 与文档

- 新增 `decisions/ADR-003-retrieval.md`（ADR-001 D-3 已为"检索引擎选型"预留 ADR-003 编号）：记录引入向量混合检索、RRF 选型、Proxy/本地兜底、不做 chunking 的理由。
- `AGENTS.md`：改"检索方案锁定 PageIndex…不走向量检索"为"PageIndex 树 + BM25/向量混合检索（RRF）"。
- ADR-001 不动（它不含"不走向量"锁，仅 D-3 提及 v0 用 BM25）。

## 10. 不做（YAGNI）

- 不做长节点 chunking 多向量（评测确认漏召回再加）。
- 不引入 faiss/向量数据库（~700 向量内存点积足够）。
- 不引入向量做 reranker / cross-encoder。
- 不动 agent 工具面、提示词、SSE、前端、引用链路。
- 不在本 spec 做反馈学习（另开 spec，依赖本层）。

## 11. 验收标准

- [ ] 同批入库产出 BM25 + 向量两套索引；向量失败不阻断 BM25 入库
- [ ] `search_nodes` 走 RRF 融合，返回结构与纯 BM25 逐字段一致（前端/SSE 无感）
- [ ] 概念性/换说法查询召回较纯 BM25 提升（用一组样例查询对比）
- [ ] 关闭开关 / embedding 不可用 / 索引缺失 → 优雅退回纯 BM25，不崩
- [ ] 单测覆盖 `rrf_fuse` / 向量召回 / 降级 / 缓存；集成覆盖 `search_nodes` 融合与结构不变
- [ ] ADR-001 标 superseded + ADR-002 + CLAUDE.md 同步

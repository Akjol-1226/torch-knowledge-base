import json
import re
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

from .bm25_index import BM25Index
from .embed import get_embed_client
from .fusion import rrf_fuse
from .snippet import make_snippet
from .tokenize import load_dict
from .vector_index import VectorIndex

log = get_logger("retrieval.treestore")

# 通用占位标题（附表1 / 附件2 / 表3 / 图1 等）不参与"同名窗口"分组：
# 这类标题在不同段落里会重复出现，按标题相等合并会把不相干的表错并成一段。
_GENERIC_TITLE = re.compile(r"^(附表|附件|表|图)\s*\d*$")


def _is_groupable_title(title: str) -> bool:
    t = (title or "").strip()
    # 用 _norm_title 归一后再判通用占位：与合并键(_norm_title)口径一致，避免"附 表1"这类
    # 畸形空格漏过通用判定、被当可分组标题而错并不相干的表。
    return bool(t) and not _GENERIC_TITLE.match(_norm_title(title))


def _norm_title(title: str) -> str:
    """同名窗口分组用的标题归一:去掉所有空白。
    解析常把同一工序的多段窗口标题写得空格不一致(如「G03 涂布工序工艺规程」vs
    「G03涂布工序工艺规程」),精确相等会把本应连成一段的 span 切断 → 用它归一后再比。"""
    return re.sub(r"\s+", "", title or "")


class TreeStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self._catalog = []
        self._docs = {}          # doc_id -> doc dict
        self._nodes = {}         # handle -> record
        self._top = {}           # doc_id -> [handle,...] 顶层
        self._doc_handles = {}   # doc_id -> [handle,...] 该文档全部节点（增量移除用）
        self._doc_mtime = {}     # doc_id -> workspace json mtime（增量刷新差量判定）
        self._bm25 = None
        self._vec = None         # VectorIndex | None（缺失 → 纯 BM25）
        self._load()

    # ---- 加载 ----
    def _load(self):
        # 先加载域词典：让查询端分词与入库端一致（型号/图号保持整词，否则搜不到）
        load_dict(self.data_dir / "domain_dict_auto.txt")
        self._load_catalog()
        ws = self.data_dir / "workspace"
        for f in sorted(ws.glob("doc_*.json")) if ws.exists() else []:
            self._load_doc(f)
        self._load_indexes()

    def _load_catalog(self):
        cat_path = self.data_dir / "catalog" / "document_catalog.json"
        self._catalog = (
            json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else []
        )

    def _load_doc(self, f):
        """读一个 workspace/doc_*.json 并索引其节点，记录文件 mtime（增量刷新基准）。"""
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("workspace_doc_unreadable_skipped", path=str(f))
            return
        self._docs[doc["id"]] = doc
        self._index_doc(doc)
        try:
            self._doc_mtime[doc["id"]] = f.stat().st_mtime
        except OSError:
            pass

    def _load_indexes(self):
        idx_dir = self.data_dir / "indexes"
        # BM25 缺失（如删到一篇不剩）要置 None，不能留旧索引
        self._bm25 = BM25Index.load(idx_dir) if (idx_dir / "meta.json").exists() else None
        # 向量索引为增强项：缺失/损坏 → None → search_nodes 退回纯 BM25
        self._vec = VectorIndex.load(idx_dir)

    def _remove_doc(self, doc_id):
        """从内存索引里摘除一个文档的全部节点（增量刷新：删除/变更前先清旧）。"""
        for h in self._doc_handles.pop(doc_id, []):
            self._nodes.pop(h, None)
        self._top.pop(doc_id, None)
        self._docs.pop(doc_id, None)
        self._doc_mtime.pop(doc_id, None)

    def incremental_clone(self):
        """返回一个增量刷新后的【新】TreeStore：复用本实例里未变文档的节点结构，
        只重读 workspace 里新增/变更的文档、摘除已删的。新实例构建好后由调用方原子发布，
        避免就地修改导致并发查询读到半更新状态（沿用"先建好再发布"语义，但省去全量重解析）。"""
        new = TreeStore.__new__(TreeStore)
        new.data_dir = self.data_dir
        new._catalog = []
        # 按 doc 粒度整体替换/删除，不改到旧实例仍在用的子结构 → 浅拷贝足够
        new._docs = dict(self._docs)
        new._nodes = dict(self._nodes)
        new._top = dict(self._top)
        new._doc_handles = dict(self._doc_handles)
        new._doc_mtime = dict(self._doc_mtime)
        new._bm25 = None
        new._vec = None
        new._apply_refresh()
        return new

    def _apply_refresh(self):
        """对已拷贝的内存状态做差量更新：域词典/目录/BM25/向量为全局产物整体重载
        （远比逐文档重解析树便宜），workspace 仅处理新增/变更/删除的文档。"""
        load_dict(self.data_dir / "domain_dict_auto.txt")
        self._load_catalog()
        ws = self.data_dir / "workspace"
        current = {f.stem: f for f in ws.glob("doc_*.json")} if ws.exists() else {}
        for doc_id in list(self._doc_mtime):  # 已删除的文档：摘除其节点
            if doc_id not in current:
                self._remove_doc(doc_id)
        for doc_id, f in current.items():      # 新增/变更的文档：重读重建
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if self._doc_mtime.get(doc_id) == mt:
                continue
            if doc_id in self._doc_mtime:
                self._remove_doc(doc_id)       # 变更：先清旧节点再重新索引
            self._load_doc(f)
        self._load_indexes()

    def _index_doc(self, doc):
        doc_id = doc["id"]
        doc_name = doc.get("doc_name", "")
        doc_kb = doc.get("kb", "default")
        line_count = doc.get("line_count", 0)
        order = []   # 文档序的 (handle, line_num)，用于算行范围

        def walk(nodes, parent_handle, path_titles):
            handles = []
            for i, n in enumerate(nodes):
                h = f"{doc_id}:{n['node_id']}"
                handles.append(h)
                my_path = path_titles + [n.get("title", "")]
                self._nodes[h] = {
                    "handle": h, "doc_id": doc_id, "doc_name": doc_name, "kb": doc_kb,
                    "title": n.get("title", ""), "summary": n.get("summary", "") or "",
                    "text": n.get("text", "") or "", "line_num": n.get("line_num", 0),
                    "page": n.get("page"),
                    "parent": parent_handle, "path_titles": my_path,
                    "child_handles": [], "prev": None, "next": None,
                }
                order.append((h, n.get("line_num", 0)))
                child_handles = walk(n.get("nodes", []), h, my_path)
                self._nodes[h]["child_handles"] = child_handles
                # 兄弟 prev/next
                if i > 0:
                    self._nodes[h]["prev"] = handles[i - 1]
                    self._nodes[handles[i - 1]]["next"] = h
            return handles

        self._top[doc_id] = walk(doc.get("structure", []), None, [doc_name])

        # 该文档全部节点句柄（增量刷新摘除本文档时用）
        self._doc_handles[doc_id] = [h for h, _ in order]

        # 行范围：按文档序，end = 下一节点 line_num - 1；末节点 = line_count
        order.sort(key=lambda x: x[1])
        for idx, (h, ln) in enumerate(order):
            end = (order[idx + 1][1] - 1) if idx + 1 < len(order) else line_count
            if end < ln:
                end = ln
            self._nodes[h]["lines"] = f"{ln}-{end}"

    # ---- 工具方法 ----
    def list_catalog(self, kbs=None):
        # kbs 非空时只返回这些知识库的文档（检索范围隔离）
        if kbs:
            allow = set(kbs)
            return [c for c in self._catalog if c.get("kb", "default") in allow]
        return self._catalog

    def _brief(self, h):
        n = self._nodes[h]
        return {"id": h, "title": n["title"], "summary": n["summary"],
                "lines": n.get("lines", ""), "has_children": bool(n["child_handles"])}

    def get_outline(self, doc_id):
        doc = self._docs.get(doc_id)
        if not doc:
            return {"error": f"unknown doc: {doc_id}"}
        return {"doc": doc_id, "name": doc.get("doc_name", ""),
                "nodes": [self._brief(h) for h in self._top.get(doc_id, [])]}

    def open_node(self, node_id):
        n = self._nodes.get(node_id)
        if not n:
            return {"error": f"unknown node: {node_id}"}
        return {"node": node_id, "title": n["title"],
                "children": [self._brief(c) for c in n["child_handles"]]}

    def read_node(self, node_id):
        n = self._nodes.get(node_id)
        if not n:
            return {"error": f"unknown node: {node_id}"}
        out = {
            "id": node_id, "title": n["title"], "text": n["text"],
            "cite": {"doc": n["doc_name"], "section": n["title"], "lines": n.get("lines", ""),
                     "handle": node_id, "doc_id": n["doc_id"], "page": n.get("page"),
                     "snippet": make_snippet(n["text"], "")},
            "path": " > ".join(n["path_titles"]),
            "parent_id": n["parent"], "prev_id": n["prev"], "next_id": n["next"],
            "has_children": bool(n["child_handles"]),
        }
        sec = self._section_info(node_id)
        if sec:
            out["section"] = sec
        return out

    def _node(self, node_id):
        return self._nodes.get(node_id)

    def _section_members(self, node_id):
        """一个长工序/章节常被切成多个【连续同名兄弟窗口】。返回与本节点标题相同、
        在兄弟链上首尾相连的那一串窗口 handle（含自身，按文档序，不含子节点）。
        通用占位标题（附表N 等）不分组，避免把不相干的表错并成一段。"""
        n = self._nodes.get(node_id)
        if not n:
            return [node_id]
        title = (n["title"] or "").strip()
        if not _is_groupable_title(title):
            return [node_id]
        ntitle = _norm_title(title)   # 空格归一:G03 涂布… 与 G03涂布… 视为同一工序窗口
        members = [node_id]
        p = n["prev"]
        while p and _norm_title(self._nodes.get(p, {}).get("title")) == ntitle:
            members.insert(0, p)
            p = self._nodes[p]["prev"]
        nx = n["next"]
        while nx and _norm_title(self._nodes.get(nx, {}).get("title")) == ntitle:
            members.append(nx)
            nx = self._nodes[nx]["next"]
        return members

    def _descendants(self, h):
        out = []
        for c in self._nodes.get(h, {}).get("child_handles", []):
            out.append(c)
            out.extend(self._descendants(c))
        return out

    def _section_info(self, node_id):
        """若本节点属于一个跨窗口段落（同名窗口 > 1），返回 {part,total,span}，否则 None。
        span 含该段所有窗口【以及窗口下的子节点（附表/附件等）】，按文档序——因为参数表
        往往挂在窗口的 children 上，只读窗口会漏掉真正的数据。"""
        members = self._section_members(node_id)
        if len(members) <= 1:
            return None
        handles = list(members)
        for m in members:
            handles.extend(self._descendants(m))
        handles = sorted(set(handles), key=lambda h: self._nodes[h]["line_num"])
        # members: 仅本段各窗口（不含附表等子节点），按文档序——「查看结构」显示窗口正文用
        # （附表作为树上子节点单独展开，不在窗口正文里重复）；span: 窗口+子节点全集（引用/补读用）。
        members = sorted(members, key=lambda h: self._nodes[h]["line_num"])
        return {"part": members.index(node_id) + 1, "total": len(members),
                "members": members, "span": handles}

    def _vector_search(self, query: str, top_n: int) -> list:
        """向量召回 id 列表；任何不可用（无索引/无 client/构建/编码失败/空间不匹配）都返回 []。"""
        if self._vec is None:
            return []
        # 整段（取 client + 编码 + 向量检索）都纳入兜底：任何失败都退回纯 BM25，不让查询 500
        try:
            client = get_embed_client()
            if client is None:
                return []
            if self._vec.signature != client.signature:
                # 索引与当前模型向量空间不一致（换过 embedding 模型）→ 跳过向量，待重建
                log.warning(
                    "vector_index_signature_mismatch",
                    index=self._vec.signature, client=client.signature,
                )
                return []
            qvec = client.embed([query])
            if qvec.size == 0:
                return []
            s = get_settings()
            hits = self._vec.search(qvec[0], max(s.retrieval_top_n, top_n), s.vec_sim_threshold)
            return [d["node_id_full"] for d in hits]
        except Exception:
            log.warning("vector_search_failed_fallback_bm25", exc_info=True)
            return []

    def search_nodes(self, query: str, top_k: int = 8, kbs=None):
        # 索引缺失要显形，不能静默返回 []（否则 agent 会把"检索不可用"误判成"没找到"）
        if self._bm25 is None:
            return {"error": "检索索引未构建或未加载（请先入库 ingest）"}
        # 多取候选再按 section 折叠 + 按 kb 过滤（检索范围隔离）；kb 过滤会减少命中，故多取候选
        allow = set(kbs) if kbs else None
        s = get_settings()
        # 候选池深取：两路按同一深度（≥retrieval_top_n）取数，避免 BM25 候选被 top_k 截断、
        # 与向量取数深度不对称导致 RRF 单边失真。折叠/裁剪到 top_k 在融合之后做。
        cand = max(s.retrieval_top_n, top_k * (6 if allow else 3))
        bm25_ids = [h["node_id_full"] for h in self._bm25.search(query, top_k=cand)]
        # 混合检索：BM25 + 向量 RRF 融合；向量不可用时退回纯 BM25 顺序（结果结构不变）
        vec_ids = self._vector_search(query, cand)
        if vec_ids:
            fused = rrf_fuse(bm25_ids, vec_ids, w_bm25=s.rrf_w_bm25, w_vec=s.rrf_w_vec, k=s.rrf_k)
        else:
            fused = [(i, 0.0) for i in bm25_ids]
        out = []
        seen_section = set()
        for h, score in fused:
            n = self._nodes.get(h)
            if not n:
                continue
            if allow and n.get("kb", "default") not in allow:
                continue
            sec = self._section_info(h)
            if sec:
                key = tuple(self._section_members(h))
                if key in seen_section:
                    continue
                seen_section.add(key)
            snip = make_snippet(n["text"], query)
            item = {
                "id": h, "title": n["title"], "score": score,
                "snippet": snip,
                "cite": {"doc": n["doc_name"], "section": n["title"], "lines": n.get("lines", ""),
                         "handle": h, "doc_id": n["doc_id"], "page": n.get("page"),
                         "snippet": snip},
                "path": " > ".join(n["path_titles"]),
                "parent_id": n["parent"], "prev_id": n["prev"], "next_id": n["next"],
            }
            if sec:
                item["section"] = sec
            out.append(item)
            if len(out) >= top_k:
                break
        return out

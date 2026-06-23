import json
import re
from pathlib import Path

from .bm25_index import BM25Index
from .snippet import make_snippet
from .tokenize import load_dict

# 通用占位标题（附表1 / 附件2 / 表3 / 图1 等）不参与"同名窗口"分组：
# 这类标题在不同段落里会重复出现，按标题相等合并会把不相干的表错并成一段。
_GENERIC_TITLE = re.compile(r"^(附表|附件|表|图)\s*\d*$")


def _is_groupable_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(t) and not _GENERIC_TITLE.match(t)


class TreeStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self._catalog = []
        self._docs = {}          # doc_id -> doc dict
        self._nodes = {}         # handle -> record
        self._top = {}           # doc_id -> [handle,...] 顶层
        self._bm25 = None
        self._load()

    # ---- 加载 ----
    def _load(self):
        # 先加载域词典：让查询端分词与入库端一致（型号/图号保持整词，否则搜不到）
        load_dict(self.data_dir / "domain_dict_auto.txt")
        cat_path = self.data_dir / "catalog" / "document_catalog.json"
        if cat_path.exists():
            self._catalog = json.loads(cat_path.read_text(encoding="utf-8"))
        ws = self.data_dir / "workspace"
        for f in sorted(ws.glob("doc_*.json")) if ws.exists() else []:
            doc = json.loads(f.read_text(encoding="utf-8"))
            self._docs[doc["id"]] = doc
            self._index_doc(doc)
        idx_dir = self.data_dir / "indexes"
        if (idx_dir / "meta.json").exists():
            self._bm25 = BM25Index.load(idx_dir)

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
                     "handle": node_id, "doc_id": n["doc_id"],
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
        members = [node_id]
        p = n["prev"]
        while p and (self._nodes.get(p, {}).get("title") or "").strip() == title:
            members.insert(0, p)
            p = self._nodes[p]["prev"]
        nx = n["next"]
        while nx and (self._nodes.get(nx, {}).get("title") or "").strip() == title:
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
        return {"part": members.index(node_id) + 1, "total": len(members), "span": handles}

    def search_nodes(self, query: str, top_k: int = 8, kbs=None):
        # 索引缺失要显形，不能静默返回 []（否则 agent 会把"检索不可用"误判成"没找到"）
        if self._bm25 is None:
            return {"error": "检索索引未构建或未加载（请先入库 ingest）"}
        # 多取候选再按 section 折叠 + 按 kb 过滤（检索范围隔离）；kb 过滤会减少命中，故多取候选
        allow = set(kbs) if kbs else None
        raw = self._bm25.search(query, top_k=top_k * (6 if allow else 3))
        out = []
        seen_section = set()
        for hit in raw:
            h = hit["node_id_full"]
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
                "id": h, "title": n["title"], "score": hit["score"],
                "snippet": snip,
                "cite": {"doc": n["doc_name"], "section": n["title"], "lines": n.get("lines", ""),
                         "handle": h, "doc_id": n["doc_id"], "snippet": snip},
                "path": " > ".join(n["path_titles"]),
                "parent_id": n["parent"], "prev_id": n["prev"], "next_id": n["next"],
            }
            if sec:
                item["section"] = sec
            out.append(item)
            if len(out) >= top_k:
                break
        return out

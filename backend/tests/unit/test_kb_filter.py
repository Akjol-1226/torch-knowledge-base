"""多知识库检索隔离：search_nodes / list_catalog 按 kb 过滤。"""

import json

from app.core.retrieval.bm25_index import build_index
from app.core.retrieval.nodes import iter_nodes
from app.core.retrieval.treestore import TreeStore


def _doc(doc_id, name, kb, text):
    return {
        "id": doc_id,
        "doc_name": name,
        "kb": kb,
        "line_count": 10,
        "structure": [
            {"title": name, "node_id": "0001", "line_num": 1, "summary": "", "text": text, "nodes": []}
        ],
    }


def _build_store(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    d1 = _doc("doc_a", "瓷片工艺", "ceramic", "介质膜厚控制 50μm 回流焊峰值 245℃")
    d2 = _doc("doc_b", "检验标准", "inspect", "膜厚检验 绝缘电阻 5000MΩ")
    (ws / "doc_a.json").write_text(json.dumps(d1, ensure_ascii=False), encoding="utf-8")
    (ws / "doc_b.json").write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
    cat = [
        {"doc_id": "doc_a", "doc_name": "瓷片工艺", "kb": "ceramic"},
        {"doc_id": "doc_b", "doc_name": "检验标准", "kb": "inspect"},
    ]
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps(cat, ensure_ascii=False), encoding="utf-8"
    )
    recs = list(iter_nodes("doc_a", d1)) + list(iter_nodes("doc_b", d2))
    build_index(recs).save(tmp_path / "indexes")
    return TreeStore(tmp_path)


def test_search_nodes_filtered_by_kb(tmp_path):
    store = _build_store(tmp_path)
    # 全库搜"膜厚"应命中两个库
    all_docs = {h["cite"]["doc"] for h in store.search_nodes("膜厚", top_k=10)}
    assert "瓷片工艺" in all_docs and "检验标准" in all_docs
    # 限定 ceramic 库：只返回瓷片工艺
    ceramic = store.search_nodes("膜厚", top_k=10, kbs=["ceramic"])
    assert ceramic and all(h["cite"]["doc"] == "瓷片工艺" for h in ceramic)


def test_list_catalog_filtered_by_kb(tmp_path):
    store = _build_store(tmp_path)
    assert len(store.list_catalog()) == 2
    assert {c["doc_id"] for c in store.list_catalog(kbs=["inspect"])} == {"doc_b"}

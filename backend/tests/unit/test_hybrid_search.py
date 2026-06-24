"""TreeStore 混合检索：BM25 + 向量 RRF 融合，及向量不可用时退回纯 BM25。

构造：BM25 对 query 排序为 [d1,d2,d3]（按 title 词频），向量却把 d3 排第一。
故混合检索把 d3 顶到首位；向量关掉则回到 d1 首位。结构字段两种情况都齐全。
"""

import json

import numpy as np
import pytest

from app.core.retrieval.bm25_index import build_index
from app.core.retrieval.embed import reset_embed_client, set_embed_client
from app.core.retrieval.nodes import iter_nodes
from app.core.retrieval.treestore import TreeStore
from app.core.retrieval.vector_index import VectorIndex


class FakeEmbed:
    """节点按正文里的 M0/M1/M2 标记 onehot；查询含 QFLAG → 对齐 d3(dim2)。"""

    signature = "fake|m1"

    def embed(self, texts):
        rows = []
        for t in texts:
            if "M0" in t:
                rows.append([1.0, 0.0, 0.0])
            elif "M1" in t:
                rows.append([0.0, 1.0, 0.0])
            elif "M2" in t or "QFLAG" in t:
                rows.append([0.0, 0.0, 1.0])
            else:
                rows.append([0.0, 0.0, 0.0])
        m = np.asarray(rows, np.float32)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return (m / n).astype(np.float32)


def _data(tmp_path, with_vectors: bool):
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    (tmp_path / "indexes").mkdir(parents=True)
    doc = {
        "id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺", "line_count": 30,
        "structure": [
            {"title": "common common common", "node_id": "0001", "line_num": 1,
             "summary": "s1", "text": "M0 正文一", "nodes": []},
            {"title": "common common", "node_id": "0002", "line_num": 10,
             "summary": "s2", "text": "M1 正文二", "nodes": []},
            {"title": "common", "node_id": "0003", "line_num": 20,
             "summary": "s3", "text": "M2 正文三", "nodes": []},
        ],
    }
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_a", "doc_name": "工艺文件", "kb": "default"}], ensure_ascii=False),
        encoding="utf-8")
    recs = list(iter_nodes("doc_a", doc))
    build_index(recs).save(tmp_path / "indexes")
    if with_vectors:
        VectorIndex.build(recs, FakeEmbed(), 6000).save(tmp_path / "indexes")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_embed():
    yield
    reset_embed_client()


def test_hybrid_fusion_promotes_vector_hit(tmp_path):
    set_embed_client(FakeEmbed())
    ts = TreeStore(_data(tmp_path, with_vectors=True))
    hits = ts.search_nodes("common QFLAG", top_k=3)
    # 向量把 d3 顶到首位（BM25 单独会是 d1）
    assert hits[0]["id"] == "doc_a:0003"
    # 结构字段齐全（与纯 BM25 一致）
    top = hits[0]
    assert top["cite"]["handle"] == "doc_a:0003"
    assert top["cite"]["doc_id"] == "doc_a"
    assert top["path"].startswith("工艺文件 >")
    assert "score" in top


def test_falls_back_to_bm25_when_vector_off(tmp_path):
    set_embed_client(None)  # 向量不可用
    ts = TreeStore(_data(tmp_path, with_vectors=True))
    hits = ts.search_nodes("common QFLAG", top_k=3)
    # 纯 BM25：title 词频最高的 d1 居首
    assert hits[0]["id"] == "doc_a:0001"
    assert hits[0]["cite"]["handle"] == "doc_a:0001"


def test_no_vector_index_is_pure_bm25(tmp_path):
    set_embed_client(FakeEmbed())  # 有 client 但没建向量索引
    ts = TreeStore(_data(tmp_path, with_vectors=False))
    hits = ts.search_nodes("common QFLAG", top_k=3)
    assert hits[0]["id"] == "doc_a:0001"  # 退回 BM25


def test_signature_mismatch_skips_vector(tmp_path):
    class OtherModel(FakeEmbed):
        signature = "fake|DIFFERENT"

    set_embed_client(OtherModel())  # 索引用 fake|m1 建，client 是 fake|DIFFERENT
    ts = TreeStore(_data(tmp_path, with_vectors=True))
    hits = ts.search_nodes("common QFLAG", top_k=3)
    assert hits[0]["id"] == "doc_a:0001"  # 向量空间不匹配 → 跳过向量 → BM25

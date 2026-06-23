import json

from app.core.retrieval.bm25_index import build_index
from app.core.retrieval.nodes import iter_nodes
from app.core.retrieval.treestore import TreeStore


def _data(tmp_path):
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    (tmp_path / "indexes").mkdir(parents=True)
    doc = {"id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺",
           "line_count": 20,
           "structure": [
               {"title": "5 SMT", "node_id": "0001", "line_num": 1, "summary": "贴片",
                "text": "本章", "nodes": [
                   {"title": "5.3 回流焊", "node_id": "0003", "line_num": 8, "summary": "回流曲线",
                    "text": "峰值温度 245℃，217℃以上停留 60到90秒", "nodes": []}]},
               {"title": "6 检验", "node_id": "0004", "line_num": 15, "summary": "检验",
                "text": "外观与电性能检验", "nodes": []}]}
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺"}], ensure_ascii=False),
        encoding="utf-8")
    recs = list(iter_nodes("doc_a", doc))
    build_index(recs).save(tmp_path / "indexes")
    return tmp_path


def test_search_nodes_enriched_hit(tmp_path):
    ts = TreeStore(_data(tmp_path))
    hits = ts.search_nodes("回流焊 峰值温度", top_k=2)
    top = hits[0]
    assert top["id"] == "doc_a:0003"
    assert "245" in top["snippet"]
    assert top["cite"]["section"] == "5.3 回流焊"
    assert top["path"] == "工艺文件 > 5 SMT > 5.3 回流焊"
    assert top["parent_id"] == "doc_a:0001"
    # 引用锚点字段：handle / doc_id / snippet 进 cite
    assert top["cite"]["handle"] == "doc_a:0003"
    assert top["cite"]["doc_id"] == "doc_a"
    assert "245" in top["cite"]["snippet"]

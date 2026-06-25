from app.core.retrieval.bm25_index import BM25Index, build_index
from app.core.retrieval.nodes import iter_nodes


def _tree():
    return {"structure": [
        {"title": "5 SMT工艺", "node_id": "0005", "summary": "贴片回流", "text": "本章工艺",
         "nodes": [
            {"title": "5.3 回流焊", "node_id": "0008", "summary": "回流曲线",
             "text": "峰值温度 245℃，217℃以上停留 60到90秒", "nodes": []}]},
        {"title": "6 检验", "node_id": "0010", "summary": "检验项目",
         "text": "外观与电性能检验", "nodes": []},
    ]}


def test_iter_nodes_flattens_with_handles():
    recs = list(iter_nodes("doc_x", _tree()))
    handles = {r["node_id_full"] for r in recs}
    assert handles == {"doc_x:0005", "doc_x:0008", "doc_x:0010"}


def test_iter_nodes_context_is_docname_plus_ancestors():
    tree = {"doc_name": "工艺文件A", **_tree()}
    recs = {r["node_id_full"]: r for r in iter_nodes("doc_x", tree)}
    # 顶层节点：只有文档名（无祖先标题）
    assert recs["doc_x:0005"]["context"] == "工艺文件A"
    # 子节点：文档名 + 父标题链，不含自身标题
    assert recs["doc_x:0008"]["context"] == "工艺文件A > 5 SMT工艺"


def test_search_ranks_relevant_node_first():
    recs = list(iter_nodes("doc_x", _tree()))
    idx = build_index(recs)
    hits = idx.search("回流焊 峰值温度", top_k=2)
    assert hits[0]["node_id_full"] == "doc_x:0008"


def test_save_load_roundtrip(tmp_path):
    recs = list(iter_nodes("doc_x", _tree()))
    build_index(recs).save(tmp_path / "idx")
    idx2 = BM25Index.load(tmp_path / "idx")
    hits = idx2.search("检验", top_k=1)
    assert hits[0]["node_id_full"] == "doc_x:0010"


def test_load_corrupt_returns_none(tmp_path):
    # 重建中途读到 / 文件损坏 → load 返回 None（上层退化为索引不可用，不 500）
    recs = list(iter_nodes("doc_x", _tree()))
    build_index(recs).save(tmp_path / "idx")
    (tmp_path / "idx" / "meta.json").write_text("{ 截断的坏 JSON", encoding="utf-8")
    assert BM25Index.load(tmp_path / "idx") is None

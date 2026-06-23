from pathlib import Path

from app.modules.ingest import tree as tree_mod


def test_build_tree_returns_structure(tmp_path, monkeypatch):
    md = tmp_path / "demo.md"
    md.write_text("# 标题A\n正文a\n## 子标题A1\n正文a1\n# 标题B\n正文b\n", encoding="utf-8")

    async def fake_md_to_tree(md_path, **kwargs):
        return {
            "doc_name": Path(md_path).stem,
            "doc_description": "测试文档",
            "line_count": 6,
            "structure": [
                {"title": "标题A", "node_id": "0001", "line_num": 1,
                 "summary": "A的摘要", "text": "正文a",
                 "nodes": [{"title": "子标题A1", "node_id": "0002", "line_num": 3,
                            "summary": "A1摘要", "text": "正文a1", "nodes": []}]},
                {"title": "标题B", "node_id": "0003", "line_num": 5,
                 "summary": "B的摘要", "text": "正文b", "nodes": []},
            ],
        }

    monkeypatch.setattr(tree_mod, "md_to_tree", fake_md_to_tree)

    result = tree_mod.build_tree(str(md), model="fake-model")
    assert result["doc_name"] == "demo"
    assert result["doc_description"] == "测试文档"
    assert len(result["structure"]) == 2
    assert result["structure"][0]["nodes"][0]["title"] == "子标题A1"

from pathlib import Path

import pytest

from app.core.pageindex import utils as pageindex_utils
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


@pytest.mark.asyncio
async def test_pageindex_llm_disables_thinking_by_default(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class Choice:
            message = type("Message", (), {"content": "摘要"})()

        return type("Response", (), {"choices": [Choice()]})()

    monkeypatch.delenv("PAGEINDEX_ENABLE_THINKING", raising=False)
    monkeypatch.setattr(pageindex_utils.litellm, "acompletion", fake_acompletion)

    result = await pageindex_utils.llm_acompletion("openai/qwen3.7-max", "总结")

    assert result == "摘要"
    assert captured["extra_body"] == {"enable_thinking": False}

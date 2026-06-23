import json

from app.core.retrieval.treestore import TreeStore
from app.modules.chat import tools as T


def _store(tmp_path):
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    doc = {"id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺", "line_count": 10,
           "structure": [{"title": "5.3 回流焊", "node_id": "0003", "line_num": 1,
                          "summary": "回流", "text": "峰值 245℃", "nodes": []}]}
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺"}], ensure_ascii=False),
        encoding="utf-8")
    return TreeStore(tmp_path)


def test_tools_invoke_via_store(tmp_path):
    T.set_store(_store(tmp_path))
    assert T.list_catalog.invoke({})[0]["doc_name"] == "工艺文件"
    assert T.get_outline.invoke({"doc_id": "doc_a"})["nodes"][0]["id"] == "doc_a:0003"
    r = T.read_node.invoke({"node_id": "doc_a:0003"})
    assert r["text"] == "峰值 245℃" and r["cite"]["section"] == "5.3 回流焊"


def test_kb_tools_list():
    names = {t.name for t in T.KB_TOOLS}
    assert names == {"list_catalog", "get_outline", "open_node", "read_node", "search_nodes"}

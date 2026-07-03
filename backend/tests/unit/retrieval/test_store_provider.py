import json

from app.core.retrieval import store_provider
from app.core.retrieval.treestore import TreeStore


def _make_data_dir(tmp_path):
    ws = tmp_path / "workspace"
    cat = tmp_path / "catalog"
    idx = tmp_path / "indexes"
    ws.mkdir(parents=True)
    cat.mkdir(parents=True)
    idx.mkdir(parents=True)
    doc = {
        "id": "doc_a",
        "doc_name": "工艺文件",
        "doc_description": "工艺",
        "kb": "default",
        "line_count": 3,
        "structure": [
            {
                "title": "1 范围",
                "node_id": "0001",
                "line_num": 1,
                "summary": "范围摘要",
                "text": "0805 产品",
                "nodes": [],
            }
        ],
    }
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (cat / "document_catalog.json").write_text(
        json.dumps(
            [
                {
                    "doc_id": "doc_a",
                    "doc_name": "工艺文件",
                    "doc_description": "工艺",
                    "kb": "default",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_get_tree_store_uses_settings_data_dir(tmp_path, monkeypatch):
    data_dir = _make_data_dir(tmp_path)
    monkeypatch.setattr(
        store_provider,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": data_dir})(),
    )
    store_provider.clear_tree_store_for_tests()

    store = store_provider.get_tree_store()

    assert isinstance(store, TreeStore)
    assert store.list_catalog()[0]["doc_id"] == "doc_a"


def test_set_tree_store_for_tests_injects_store(tmp_path, monkeypatch):
    data_dir = _make_data_dir(tmp_path)
    injected = TreeStore(data_dir)
    monkeypatch.setattr(
        store_provider,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": data_dir})(),
    )

    store_provider.set_tree_store_for_tests(injected)

    assert store_provider.get_tree_store() is injected

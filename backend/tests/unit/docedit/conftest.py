from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.retrieval.store_provider import clear_tree_store_for_tests


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    clear_tree_store_for_tests()
    assert get_settings().data_dir == tmp_path
    yield tmp_path
    clear_tree_store_for_tests()
    get_settings.cache_clear()


def write_sample_doc(data_dir: Path) -> dict:
    md_path = data_dir / "md" / "default" / "sample.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        "# Sample\n"
        "intro\n"
        "## A\n"
        "old body\n"
        "line two\n"
        "## B\n"
        "next body",
        encoding="utf-8",
    )
    Path(str(md_path) + ".pagemap.json").write_text(
        json.dumps([[1, 1], [6, 2]], ensure_ascii=False),
        encoding="utf-8",
    )
    doc = {
        "id": "doc_sample",
        "type": "md",
        "kb": "default",
        "path": str(md_path),
        "doc_name": "Sample",
        "doc_description": "",
        "line_count": 7,
        "structure": [
            {
                "node_id": "0001",
                "title": "A",
                "line_num": 3,
                "text": "## A\nold body\nline two",
                "summary": "old",
                "nodes": [],
            },
            {
                "node_id": "0002",
                "title": "B",
                "line_num": 6,
                "text": "## B\nnext body",
                "summary": "next",
                "nodes": [],
            },
        ],
    }
    ws_path = data_dir / "workspace" / "doc_sample.json"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (data_dir / "catalog").mkdir(parents=True, exist_ok=True)
    (data_dir / "catalog" / "document_catalog.json").write_text(
        "[]", encoding="utf-8"
    )
    (data_dir / "workspace" / "_meta.json").write_text("{}", encoding="utf-8")
    (data_dir / "domain_dict_auto.txt").write_text("sample", encoding="utf-8")
    (data_dir / "indexes").mkdir(parents=True, exist_ok=True)
    (data_dir / "indexes" / "meta.json").write_text("idx", encoding="utf-8")
    return doc

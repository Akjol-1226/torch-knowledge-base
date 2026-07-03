import json

from app.core.retrieval import document_reader


def test_read_document_markdown_by_doc_id(tmp_path, monkeypatch):
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        json.dumps(
            {
                "id": "doc_a",
                "doc_name": "工艺文件",
                "kb": "default",
                "path": str(md),
                "line_count": 3,
                "structure": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    doc = document_reader.read_parsed_document("doc_a")

    assert doc is not None
    assert doc.doc_id == "doc_a"
    assert doc.doc_name == "工艺文件"
    assert doc.markdown == "# 工艺\n\n0805 产品"


def test_read_document_returns_none_for_unknown_doc(tmp_path, monkeypatch):
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert document_reader.read_parsed_document("missing") is None


def test_read_document_rejects_markdown_path_outside_data_md(tmp_path, monkeypatch):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        json.dumps(
            {
                "id": "doc_a",
                "doc_name": "工艺文件",
                "kb": "default",
                "path": str(outside),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert document_reader.read_parsed_document("doc_a") is None

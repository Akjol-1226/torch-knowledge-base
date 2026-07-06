from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.docedit import workspace
from app.modules.docedit.diff import text_hash
from app.modules.docedit.service import (
    DoceditBadRequest,
    DoceditConflict,
    DoceditNotFound,
    DoceditServiceUnavailable,
)

from .conftest import write_sample_doc


def test_parse_handle_returns_doc_and_node_id() -> None:
    assert workspace.parse_handle("doc_a:0001") == ("doc_a", "0001")


def test_parse_handle_rejects_malformed_handle() -> None:
    with pytest.raises(DoceditBadRequest):
        workspace.parse_handle("doc_a")


def test_load_workspace_doc_rejects_md_path_outside_data_dir(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path.parent / "outside.md"
    (ws / "doc_bad.json").write_text(
        json.dumps({"id": "doc_bad", "path": str(outside), "structure": []}),
        encoding="utf-8",
    )

    with pytest.raises(DoceditConflict):
        workspace.load_workspace_doc("doc_bad")


def test_find_node_returns_node_and_ancestors(tmp_path: Path) -> None:
    doc = write_sample_doc(tmp_path)

    node, ancestors = workspace.find_node(doc, "0002")

    assert node["title"] == "B"
    assert ancestors == []


def test_has_own_body_text_ignores_heading_only_text() -> None:
    assert workspace.has_own_body_text("## Heading") is False
    assert workspace.has_own_body_text("## Heading\nbody") is True


def test_adjacent_same_title_windows_are_not_editable(tmp_path: Path) -> None:
    doc = write_sample_doc(tmp_path)
    doc["structure"].insert(
        1,
        {
            "node_id": "0001b",
            "title": "A",
            "line_num": 6,
            "text": "## A\ncontinued body",
            "summary": "continued",
            "nodes": [],
        },
    )
    doc["structure"][2]["line_num"] = 8
    md_path = Path(doc["path"])
    md_path.write_text(
        "# Sample\nintro\n## A\nold body\nline two\n## A\ncontinued body\n## B\nnext body",
        encoding="utf-8",
    )
    doc["line_count"] = 9
    (tmp_path / "workspace" / "doc_sample.json").write_text(
        json.dumps(doc, ensure_ascii=False),
        encoding="utf-8",
    )

    node = workspace.read_editable_node("doc_sample:0001")

    assert node["editable"] is False
    assert node["editable_reason"] == "merged_section"
    with pytest.raises(DoceditConflict):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "0001",
            text_hash("old body\nline two"),
            "new body",
            rebuild=lambda: {"docs": 1, "nodes": 3},
        )


def test_adjacent_same_title_window_can_be_edited_as_concrete_member(tmp_path: Path) -> None:
    doc = write_sample_doc(tmp_path)
    doc["structure"].insert(
        1,
        {
            "node_id": "0001b",
            "title": "A",
            "line_num": 6,
            "text": "## A\ncontinued body",
            "summary": "continued",
            "nodes": [],
        },
    )
    doc["structure"][2]["line_num"] = 8
    md_path = Path(doc["path"])
    md_path.write_text(
        "# Sample\nintro\n## A\nold body\nline two\n## A\ncontinued body\n## B\nnext body",
        encoding="utf-8",
    )
    doc["line_count"] = 9
    (tmp_path / "workspace" / "doc_sample.json").write_text(
        json.dumps(doc, ensure_ascii=False),
        encoding="utf-8",
    )

    node = workspace.read_editable_node("doc_sample:0001", allow_merged_member=True)

    assert node["editable"] is True
    assert node["editable_reason"] is None
    workspace.publish_workspace_edit_sync(
        "doc_sample",
        "0001",
        text_hash("old body\nline two"),
        "new body",
        rebuild=lambda: {"docs": 1, "nodes": 3},
        allow_merged_member=True,
    )

    md = md_path.read_text(encoding="utf-8")
    assert "## A\nnew body\n## A\ncontinued body" in md


def test_replace_leaf_body_preserves_heading(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)

    result = workspace.publish_workspace_edit_sync(
        "doc_sample",
        "0001",
        text_hash("old body\nline two"),
        "new body\nline two",
        rebuild=lambda: {"docs": 1, "nodes": 2},
    )

    ws_doc = workspace.load_workspace_doc("doc_sample")
    md = Path(ws_doc["path"]).read_text(encoding="utf-8")
    node, _ = workspace.find_node(ws_doc, "0001")
    assert result.line_delta == 0
    assert node["text"] == "## A\nnew body\nline two"
    assert "## A\nnew body\nline two\n## B" in md


def test_line_number_shift_updates_following_nodes(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)

    result = workspace.publish_workspace_edit_sync(
        "doc_sample",
        "0001",
        text_hash("old body\nline two"),
        "new body\nline two\nextra",
        rebuild=lambda: {"docs": 1, "nodes": 2},
    )

    ws_doc = workspace.load_workspace_doc("doc_sample")
    node_b, _ = workspace.find_node(ws_doc, "0002")
    assert result.line_delta == 1
    assert ws_doc["line_count"] == 8
    assert node_b["line_num"] == 7


def test_pagemap_line_shift_updates_breakpoints_after_edited_block(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)

    workspace.publish_workspace_edit_sync(
        "doc_sample",
        "0001",
        text_hash("old body\nline two"),
        "new body\nline two\nextra",
        rebuild=lambda: {"docs": 1, "nodes": 2},
    )

    md_path = Path(workspace.load_workspace_doc("doc_sample")["path"])
    pagemap = json.loads(Path(str(md_path) + ".pagemap.json").read_text(encoding="utf-8"))
    assert pagemap == [[1, 1], [7, 2]]


def test_markdown_workspace_mismatch_is_rejected(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)
    md_path = Path(workspace.load_workspace_doc("doc_sample")["path"])
    md_path.write_text(md_path.read_text(encoding="utf-8").replace("old body", "changed"), encoding="utf-8")

    with pytest.raises(DoceditConflict):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "0001",
            text_hash("old body\nline two"),
            "new body",
            rebuild=lambda: {"docs": 1, "nodes": 2},
        )


def test_old_block_with_nested_heading_is_rejected(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)
    ws_doc = workspace.load_workspace_doc("doc_sample")
    node, _ = workspace.find_node(ws_doc, "0001")
    node["text"] = "## A\nold body\n### nested"
    Path(tmp_path / "workspace" / "doc_sample.json").write_text(
        json.dumps(ws_doc, ensure_ascii=False), encoding="utf-8"
    )
    Path(ws_doc["path"]).write_text(
        "# Sample\nintro\n## A\nold body\n### nested\n## B\nnext body",
        encoding="utf-8",
    )

    with pytest.raises(DoceditConflict):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "0001",
            text_hash("old body\n### nested"),
            "new body",
            rebuild=lambda: {"docs": 1, "nodes": 2},
        )


def test_snapshot_restore_restores_md_workspace_catalog_domain_dict_and_indexes(
    tmp_path: Path,
) -> None:
    write_sample_doc(tmp_path)
    before_md = Path(workspace.load_workspace_doc("doc_sample")["path"]).read_text(
        encoding="utf-8"
    )

    def fail_rebuild() -> dict:
        (tmp_path / "catalog" / "document_catalog.json").write_text("changed", encoding="utf-8")
        (tmp_path / "domain_dict_auto.txt").write_text("changed", encoding="utf-8")
        (tmp_path / "indexes" / "meta.json").write_text("changed", encoding="utf-8")
        raise RuntimeError("hard rebuild failure")

    with pytest.raises(DoceditServiceUnavailable):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "0001",
            text_hash("old body\nline two"),
            "new body",
            rebuild=fail_rebuild,
        )

    assert Path(workspace.load_workspace_doc("doc_sample")["path"]).read_text(
        encoding="utf-8"
    ) == before_md
    assert (tmp_path / "catalog" / "document_catalog.json").read_text(encoding="utf-8") == "[]"
    assert (tmp_path / "domain_dict_auto.txt").read_text(encoding="utf-8") == "sample"
    assert (tmp_path / "indexes" / "meta.json").read_text(encoding="utf-8") == "idx"


def test_publish_workspace_edit_rechecks_active_reparse_inside_index_lock(
    tmp_path: Path,
) -> None:
    write_sample_doc(tmp_path)
    before_md = Path(workspace.load_workspace_doc("doc_sample")["path"]).read_text(
        encoding="utf-8"
    )

    with pytest.raises(DoceditConflict):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "0001",
            text_hash("old body\nline two"),
            "new body",
            rebuild=lambda: {"docs": 1, "nodes": 2},
            active_reparse_check=lambda: True,
        )

    assert Path(workspace.load_workspace_doc("doc_sample")["path"]).read_text(
        encoding="utf-8"
    ) == before_md


def test_publish_workspace_edit_raises_not_found_for_unknown_node(tmp_path: Path) -> None:
    write_sample_doc(tmp_path)

    with pytest.raises(DoceditNotFound):
        workspace.publish_workspace_edit_sync(
            "doc_sample",
            "missing",
            text_hash("x"),
            "new",
            rebuild=lambda: {"docs": 1, "nodes": 2},
        )

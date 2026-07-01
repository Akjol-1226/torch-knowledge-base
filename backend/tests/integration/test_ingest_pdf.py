"""PDF 直传 + notsure 审核闸门集成测试。

用 stub 替换真实 VLM 解析与建树（不调外部），验证完整闭环：
上传→按 notsure 分流（待审 / 直接建树）→审核 approve 替换 notsure 写回 md 建树。
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.modules.ingest import (
    docparse_service,
    document_service,
    reparse_worker,
    review_service,
    task_service,
    tree_service,
)


@pytest.fixture(autouse=True)
def _isolate_data(monkeypatch, tmp_path):
    """每个用例用独立 tmp 作为 data_dir，并清 settings 缓存。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _stub_convert(monkeypatch, md_body: str):
    def fake(pdf_path, output_path, title=None):
        Path(output_path).write_text(md_body, encoding="utf-8")

    monkeypatch.setattr(docparse_service, "convert_pdf_to_markdown", fake)


def test_upload_pdf_with_notsure_goes_to_review(monkeypatch, tmp_path):
    _stub_convert(
        monkeypatch,
        "# 逐批检验\n膜厚 <notsure>50±5μm</notsure>，电压 <notsure>看不清</notsure> V",
    )
    client = TestClient(app)
    r = client.post(
        "/ingest/upload-pdf",
        files={"file": ("逐批检验.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200
    # 异步：立即返回 task_id + queued，后台 worker 解析（TestClient 下已跑完）
    tid = r.json()["task_id"]
    assert r.json()["status"] == "queued"
    task = client.get(f"/ingest/tasks/{tid}").json()
    assert task["status"] == "needs_review"
    assert task["notsure_count"] == 2
    # 待审区文件已落盘，且未进 data/md（未入库）
    assert (tmp_path / "pending" / "逐批检验.md").exists()
    assert (tmp_path / "review" / "逐批检验.json").exists()
    assert not (tmp_path / "md" / "逐批检验.md").exists()


def test_upload_pdf_no_notsure_builds(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# 干净文档\n内容清晰无歧义。")
    monkeypatch.setattr(tree_service, "ingest_one", lambda md_path: {"docs": 1, "nodes": 1})
    client = TestClient(app)
    r = client.post(
        "/ingest/upload-pdf",
        files={"file": ("clean.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200
    tid = r.json()["task_id"]
    task = client.get(f"/ingest/tasks/{tid}").json()
    assert task["status"] == "done"  # 无 notsure → 直接入库
    assert task["kb"] == "default"
    assert (tmp_path / "md" / "default" / "clean.md").exists()  # 按 kb 落子目录
    # 任务出现在列表
    assert any(t["id"] == tid for t in client.get("/ingest/tasks").json())


def test_upload_pdf_skips_ocr_when_disabled(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# 干净文档\n内容清晰无歧义。")
    monkeypatch.setattr(tree_service, "ingest_one", lambda md_path: {"docs": 1, "nodes": 1})

    from app.modules.ingest import ocr_locate

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR should be disabled by default")

    monkeypatch.setattr(ocr_locate, "write_ocr_sidecar", fail_ocr)
    client = TestClient(app)
    r = client.post(
        "/ingest/upload-pdf",
        files={"file": ("clean.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert r.status_code == 200
    task = client.get(f"/ingest/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "done"
    assert not (tmp_path / "md" / "default" / "clean.md.ocr.json").exists()


def test_review_list_and_approve_writes_back(monkeypatch, tmp_path):
    monkeypatch.setattr(tree_service, "ingest_one", lambda md_path: {"docs": 1, "nodes": 3})
    review_service.save_pending(
        "检验记录", "膜厚 <notsure>50±5μm</notsure>，电压 <notsure>?</notsure> V"
    )

    client = TestClient(app)
    # 列待审
    lst = client.get("/ingest/review").json()
    assert any(x["doc"] == "检验记录" and x["notsure_count"] == 2 for x in lst)
    # 取条目
    detail = client.get("/ingest/review/检验记录").json()
    assert len(detail["notsure"]) == 2

    # approve：第 1 处默认取原值（标记正确），第 2 处修正为 100
    r = client.post("/ingest/review/检验记录/approve", json={"resolutions": {"2": "100"}})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "approved" and d["resolved"] == 2
    assert d["tree"] == {"docs": 1, "nodes": 3}

    md = (tmp_path / "md" / "default" / "检验记录.md").read_text(encoding="utf-8")
    assert "<notsure>" not in md  # 标记已去除
    assert "膜厚 50±5μm" in md  # 第 1 处保留原值
    assert "电压 100 V" in md  # 第 2 处采用修正值
    # 待审区已清空
    assert not (tmp_path / "pending" / "检验记录.md").exists()


def test_reparse_document_with_pdf_creates_ingest_task(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# clean\n重新解析后的内容")
    monkeypatch.setattr(tree_service, "ingest_one", lambda md_path: {"docs": 1, "nodes": 2})
    monkeypatch.setattr(reparse_worker, "_write_tmp_ocr", lambda pdf_path, tmp_md: None)
    monkeypatch.setattr(
        tree_service,
        "build_tree",
        lambda md_path, model: {
            "doc_name": Path(md_path).stem,
            "doc_description": "",
            "line_count": 2,
            "structure": [],
        },
    )
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "clean.md").write_text("# old\n旧内容", encoding="utf-8")
    (md_dir / "clean.md.ocr.json").write_text('{"old": true}', encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(
        (
            '{"id":"doc_clean","kb":"default","path":"'
            + str(md_dir / "clean.md").replace("\\", "\\\\")
            + '","doc_name":"clean","doc_description":"","line_count":1,"structure":[]}'
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    r = client.post("/ingest/document/doc_clean/reparse")

    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "queued"
    assert payload["document"] == "clean"
    task = client.get(f"/ingest/tasks/{payload['task_id']}").json()
    assert task["status"] == "done"
    assert task["filename"] == "clean.pdf"
    assert task["kb"] == "default"
    assert (md_dir / "clean.md").read_text(encoding="utf-8") == "# clean\n重新解析后的内容"
    doc = json.loads((ws_dir / "doc_clean.json").read_text(encoding="utf-8"))
    assert doc["doc_name"] == "clean"
    assert not (md_dir / "clean.md.ocr.json").exists()


def test_reparse_skips_ocr_when_disabled(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# clean\n重新解析后的内容")

    from app.modules.ingest import ocr_locate

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR should be disabled by default")

    monkeypatch.setattr(ocr_locate, "write_ocr_sidecar", fail_ocr)
    monkeypatch.setattr(
        tree_service,
        "build_tree",
        lambda md_path, model: {
            "doc_name": "clean",
            "doc_description": "",
            "line_count": 2,
            "structure": [],
        },
    )
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "clean.md").write_text("# old\n旧内容", encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(
        json.dumps(
            {
                "id": "doc_clean",
                "kb": "default",
                "path": str(md_dir / "clean.md"),
                "doc_name": "clean",
                "doc_description": "",
                "line_count": 1,
                "structure": [],
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    r = client.post("/ingest/document/doc_clean/reparse")

    assert r.status_code == 200
    task = client.get(f"/ingest/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "done"
    assert not (md_dir / "clean.md.ocr.json").exists()


def test_reparse_document_reuses_active_task(tmp_path):
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "clean.md").write_text("# old", encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(
        json.dumps(
            {
                "id": "doc_clean",
                "kb": "default",
                "path": str(md_dir / "clean.md"),
                "doc_name": "clean",
                "doc_description": "",
                "line_count": 1,
                "structure": [],
            }
        ),
        encoding="utf-8",
    )

    first = document_service.create_reparse_task("doc_clean")
    try:
        second = document_service.create_reparse_task("doc_clean")

        assert second["task_id"] == first["task_id"]
        assert second["status"] == "queued"
        assert second.get("existing") is True
        assert "tmp_path" not in second
        tasks = [
            t
            for t in task_service.list_tasks()
            if t.get("kind") == "reparse" and t.get("doc_id") == "doc_clean"
        ]
        assert len(tasks) == 1
    finally:
        if first.get("tmp_path"):
            Path(first["tmp_path"]).unlink(missing_ok=True)


def test_reparse_failure_keeps_old_md_and_workspace(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# clean\nnew content")
    monkeypatch.setattr(task_service, "MAX_RETRIES", 1)

    def fail_build_tree(md_path, model):
        raise RuntimeError("boom")

    monkeypatch.setattr(tree_service, "build_tree", fail_build_tree)
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    old_md = "# old\nold content"
    old_doc = {
        "id": "doc_clean",
        "kb": "default",
        "path": str(md_dir / "clean.md"),
        "doc_name": "clean",
        "doc_description": "old desc",
        "line_count": 2,
        "structure": [{"node_id": "old", "title": "old"}],
    }
    (md_dir / "clean.md").write_text(old_md, encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(json.dumps(old_doc), encoding="utf-8")
    before_ws = (ws_dir / "doc_clean.json").read_text(encoding="utf-8")

    client = TestClient(app)
    r = client.post("/ingest/document/doc_clean/reparse")

    assert r.status_code == 200
    task = client.get(f"/ingest/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "failed"
    assert (md_dir / "clean.md").read_text(encoding="utf-8") == old_md
    assert (ws_dir / "doc_clean.json").read_text(encoding="utf-8") == before_ws


def test_reparse_with_notsure_keeps_old_document_and_marks_reparse_task(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# clean\n<notsure>new uncertain content</notsure>")
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    old_md = "# old\nold content"
    old_doc = {
        "id": "doc_clean",
        "kb": "default",
        "path": str(md_dir / "clean.md"),
        "doc_name": "clean",
        "doc_description": "old desc",
        "line_count": 2,
        "structure": [{"node_id": "old", "title": "old"}],
    }
    (md_dir / "clean.md").write_text(old_md, encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(json.dumps(old_doc), encoding="utf-8")
    before_ws = (ws_dir / "doc_clean.json").read_text(encoding="utf-8")

    client = TestClient(app)
    r = client.post("/ingest/document/doc_clean/reparse")

    assert r.status_code == 200
    task = client.get(f"/ingest/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "needs_review"
    assert task["kind"] == "reparse"
    assert task["doc_id"] == "doc_clean"
    assert (md_dir / "clean.md").read_text(encoding="utf-8") == old_md
    assert (ws_dir / "doc_clean.json").read_text(encoding="utf-8") == before_ws
    review_doc = reparse_worker._reparse_review_doc("clean", "doc_clean")
    assert (tmp_path / "pending" / f"{review_doc}.md").read_text(
        encoding="utf-8"
    ).startswith("# clean")
    review = json.loads((tmp_path / "review" / f"{review_doc}.json").read_text(encoding="utf-8"))
    assert review["doc"] == review_doc
    assert review["source_doc"] == "clean"


def test_reparse_notsure_approve_replaces_original_doc_id(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# renamed\n<notsure>new uncertain content</notsure>")
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    old_md = "# old\nold content"
    old_doc = {
        "id": "doc_original_collision",
        "kb": "default",
        "path": str(md_dir / "clean.md"),
        "doc_name": "old clean",
        "doc_description": "old desc",
        "line_count": 2,
        "structure": [{"node_id": "old", "title": "old"}],
    }
    (md_dir / "clean.md").write_text(old_md, encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_original_collision.json").write_text(json.dumps(old_doc), encoding="utf-8")

    client = TestClient(app)
    r = client.post("/ingest/document/doc_original_collision/reparse")
    assert r.status_code == 200
    task = client.get(f"/ingest/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "needs_review"
    assert (md_dir / "clean.md").read_text(encoding="utf-8") == old_md
    review_doc = reparse_worker._reparse_review_doc("clean", "doc_original_collision")
    review = json.loads((tmp_path / "review" / f"{review_doc}.json").read_text(encoding="utf-8"))
    assert review["kind"] == "reparse"
    assert review["doc_id"] == "doc_original_collision"
    assert review["source_doc"] == "clean"

    monkeypatch.setattr(
        tree_service,
        "build_tree",
        lambda md_path, model: {
            "doc_name": "renamed title",
            "doc_description": "new desc",
            "line_count": 2,
            "structure": [{"node_id": "new", "title": "new"}],
        },
    )
    approved = client.post(f"/ingest/review/{review_doc}/approve", json={"resolutions": {}})

    assert approved.status_code == 200
    assert (md_dir / "clean.md").read_text(encoding="utf-8") == "# renamed\nnew uncertain content"
    workspace_files = sorted(p.name for p in ws_dir.glob("doc_*.json"))
    assert workspace_files == ["doc_original_collision.json"]
    doc = json.loads((ws_dir / "doc_original_collision.json").read_text(encoding="utf-8"))
    assert doc["id"] == "doc_original_collision"
    assert doc["doc_name"] == "renamed title"
    assert not (tmp_path / "pending" / f"{review_doc}.md").exists()
    assert not (tmp_path / "review" / f"{review_doc}.json").exists()


def test_review_approve_skips_ocr_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(tree_service, "ingest_one", lambda md_path: {"docs": 1, "nodes": 1})
    review_service.save_pending(
        "检验记录",
        "膜厚 <notsure>50±5μm</notsure>",
        original_name="检验记录.pdf",
        kb="default",
    )
    pdf_dir = tmp_path / "pdf" / "default"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "检验记录.pdf").write_bytes(b"%PDF-1.4 fake")

    from app.modules.ingest import ocr_locate

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR should be disabled by default")

    monkeypatch.setattr(ocr_locate, "write_ocr_sidecar", fail_ocr)

    client = TestClient(app)
    r = client.post("/ingest/review/检验记录/approve", json={"resolutions": {}})

    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert not (tmp_path / "md" / "default" / "检验记录.md.ocr.json").exists()


def test_commit_reparse_candidate_restores_index_artifacts_when_rebuild_fails(
    monkeypatch, tmp_path
):
    md_dir = tmp_path / "md" / "default"
    ws_dir = tmp_path / "workspace"
    catalog_dir = tmp_path / "catalog"
    indexes_dir = tmp_path / "indexes"
    md_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    catalog_dir.mkdir(parents=True)
    (indexes_dir / "bm25").mkdir(parents=True)
    md_path = md_dir / "clean.md"
    ws_path = ws_dir / "doc_clean.json"
    catalog_path = catalog_dir / "document_catalog.json"
    meta_path = ws_dir / "_meta.json"
    domain_path = tmp_path / "domain_dict_auto.txt"
    index_path = indexes_dir / "meta.json"
    bm25_path = indexes_dir / "bm25" / "index.json"
    md_path.write_text("# old", encoding="utf-8")
    old_doc = {"id": "doc_clean", "kb": "default", "path": str(md_path), "doc_name": "old"}
    ws_path.write_text(json.dumps(old_doc), encoding="utf-8")
    catalog_path.write_text('[{"doc_id":"doc_clean","doc_name":"old"}]', encoding="utf-8")
    meta_path.write_text('{"doc_clean":{"doc_name":"old"}}', encoding="utf-8")
    domain_path.write_text("OLDTERM\n", encoding="utf-8")
    index_path.write_text("old-index", encoding="utf-8")
    bm25_path.write_text("old-bm25", encoding="utf-8")

    def fail_rebuild():
        catalog_path.write_text('[{"doc_id":"doc_clean","doc_name":"new"}]', encoding="utf-8")
        meta_path.write_text('{"doc_clean":{"doc_name":"new"}}', encoding="utf-8")
        domain_path.write_text("NEWTERM\n", encoding="utf-8")
        index_path.write_text("new-index", encoding="utf-8")
        bm25_path.write_text("new-bm25", encoding="utf-8")
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(tree_service, "rebuild_from_workspace", fail_rebuild)
    candidate = {
        "id": "doc_clean",
        "kb": "default",
        "path": str(md_path),
        "doc_name": "new",
        "doc_description": "",
        "line_count": 1,
        "structure": [],
    }

    with pytest.raises(RuntimeError, match="rebuild failed"):
        tree_service.commit_reparse_candidate(candidate, "# new")

    assert md_path.read_text(encoding="utf-8") == "# old"
    assert json.loads(ws_path.read_text(encoding="utf-8")) == old_doc
    assert catalog_path.read_text(encoding="utf-8") == '[{"doc_id":"doc_clean","doc_name":"old"}]'
    assert meta_path.read_text(encoding="utf-8") == '{"doc_clean":{"doc_name":"old"}}'
    assert domain_path.read_text(encoding="utf-8") == "OLDTERM\n"
    assert index_path.read_text(encoding="utf-8") == "old-index"
    assert bm25_path.read_text(encoding="utf-8") == "old-bm25"


def test_reparse_needs_review_task_blocks_new_reparse(tmp_path):
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "clean.md").write_text("# old", encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(
        json.dumps(
            {
                "id": "doc_clean",
                "kb": "default",
                "path": str(md_dir / "clean.md"),
                "doc_name": "clean",
                "doc_description": "",
                "line_count": 1,
                "structure": [],
            }
        ),
        encoding="utf-8",
    )
    task = task_service.create("clean.pdf", "default", kind="reparse", doc_id="doc_clean")
    task_service.update(task["id"], status=task_service.NEEDS_REVIEW)

    rec = document_service.create_reparse_task("doc_clean")

    assert rec["task_id"] == task["id"]
    assert rec["status"] == task_service.NEEDS_REVIEW
    assert rec.get("existing") is True
    assert "tmp_path" not in rec
    tasks = [
        t
        for t in task_service.list_tasks()
        if t.get("kind") == "reparse" and t.get("doc_id") == "doc_clean"
    ]
    assert len(tasks) == 1


def test_reparse_pending_uses_unique_review_doc_for_same_stem(monkeypatch, tmp_path):
    _stub_convert(monkeypatch, "# clean\n<notsure>uncertain</notsure>")
    stem = "x" * 64
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / f"{stem}.md").write_text("# old", encoding="utf-8")
    (pdf_dir / f"{stem}.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean_a.json").write_text(
        json.dumps(
            {
                "id": "doc_clean_a",
                "kb": "default",
                "path": str(md_dir / f"{stem}.md"),
                "doc_name": stem,
                "doc_description": "",
                "line_count": 1,
                "structure": [],
            }
        ),
        encoding="utf-8",
    )
    (ws_dir / "doc_clean_b.json").write_text(
        json.dumps(
            {
                "id": "doc_clean_b",
                "kb": "default",
                "path": str(md_dir / f"{stem}.md"),
                "doc_name": stem,
                "doc_description": "",
                "line_count": 1,
                "structure": [],
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    first = client.post("/ingest/document/doc_clean_a/reparse")
    second = client.post("/ingest/document/doc_clean_b/reparse")

    assert first.status_code == 200
    assert second.status_code == 200
    expected_a = reparse_worker._reparse_review_doc(stem, "doc_clean_a")
    expected_b = reparse_worker._reparse_review_doc(stem, "doc_clean_b")
    assert len(expected_a) <= 64
    assert len(expected_b) <= 64
    assert expected_a != expected_b
    pending_files = sorted(p.name for p in (tmp_path / "pending").glob("*.md"))
    review_files = sorted(p.name for p in (tmp_path / "review").glob("*.json"))
    assert pending_files == sorted([f"{expected_a}.md", f"{expected_b}.md"])
    assert review_files == sorted([f"{expected_a}.json", f"{expected_b}.json"])


def test_reparse_document_without_pdf_returns_404(tmp_path):
    md_dir = tmp_path / "md" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "legacy.md").write_text("# legacy", encoding="utf-8")
    (ws_dir / "doc_legacy.json").write_text(
        (
            '{"id":"doc_legacy","kb":"default","path":"'
            + str(md_dir / "legacy.md").replace("\\", "\\\\")
            + '","doc_name":"legacy","doc_description":"","line_count":1,"structure":[]}'
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    r = client.post("/ingest/document/doc_legacy/reparse")

    assert r.status_code == 404
    assert "原 PDF" in r.json()["detail"]


def test_document_tree_exposes_has_pdf(tmp_path):
    md_dir = tmp_path / "md" / "default"
    pdf_dir = tmp_path / "pdf" / "default"
    ws_dir = tmp_path / "workspace"
    md_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    ws_dir.mkdir(parents=True)
    (md_dir / "clean.md").write_text("# clean", encoding="utf-8")
    (pdf_dir / "clean.pdf").write_bytes(b"%PDF-1.4 fake")
    (ws_dir / "doc_clean.json").write_text(
        (
            '{"id":"doc_clean","kb":"default","path":"'
            + str(md_dir / "clean.md").replace("\\", "\\\\")
            + '","doc_name":"clean","doc_description":"","line_count":1,"structure":[]}'
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    r = client.get("/ingest/document/doc_clean/tree")

    assert r.status_code == 200
    assert r.json()["has_pdf"] is True

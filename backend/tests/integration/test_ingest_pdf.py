"""PDF 直传 + notsure 审核闸门集成测试。

用 stub 替换真实 VLM 解析与建树（不调外部），验证完整闭环：
上传→按 notsure 分流（待审 / 直接建树）→审核 approve 替换 notsure 写回 md 建树。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.modules.ingest import docparse_service, review_service, tree_service


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

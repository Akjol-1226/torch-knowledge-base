import json
from pathlib import Path

from app.modules.ingest import tree_service as run_mod


def test_ingest_dir_writes_all_artifacts(tmp_path, monkeypatch):
    md_dir = tmp_path / "md"; md_dir.mkdir()
    (md_dir / "工艺文件NPD9001.md").write_text(
        "# 5 SMT\n## 5.3 回流焊\n峰值 245℃ 型号 DEMO1-0603-2X1-50V-0.10n\n",
        encoding="utf-8")

    def fake_build_tree(md_path, model):
        return {"doc_name": Path(md_path).stem, "doc_description": "工艺说明",
                "line_count": 3,
                "structure": [{"title": "5.3 回流焊", "node_id": "0001", "line_num": 1,
                               "summary": "回流", "text": "峰值 245℃", "nodes": []}]}

    monkeypatch.setattr(run_mod, "build_tree", fake_build_tree)
    out = tmp_path / "data"
    run_mod.ingest_dir(str(md_dir), out, model="fake")

    # 树
    metas = json.loads((out / "workspace" / "_meta.json").read_text(encoding="utf-8"))
    assert len(metas) == 1
    # catalog 卡片含 doc_id/doc_name/doc_description + kb（多库归属；不放型号等噪声字段）
    cat = json.loads((out / "catalog" / "document_catalog.json").read_text(encoding="utf-8"))
    assert set(cat[0].keys()) == {"doc_id", "doc_name", "doc_description", "kb"}
    assert cat[0]["kb"] == "default"
    # 但型号/项目号仍写进 domain dict（供 BM25 认整词）
    dict_text = (out / "domain_dict_auto.txt").read_text(encoding="utf-8")
    assert "DEMO1-0603-2X1-50V-0.10n" in dict_text
    assert "NPD9001" in dict_text
    # BM25 索引文件存在
    assert (out / "indexes" / "meta.json").exists()


def test_ingest_dir_disambiguates_same_doc_name(tmp_path, monkeypatch):
    # 两个不同文件解析出相同 doc_name → 不能互相覆盖，应各得唯一 doc_id（2 篇都在）
    md_dir = tmp_path / "md"
    (md_dir / "kbA").mkdir(parents=True)
    (md_dir / "kbB").mkdir(parents=True)
    (md_dir / "kbA" / "a.md").write_text("# 标题\n正文A\n", encoding="utf-8")
    (md_dir / "kbB" / "b.md").write_text("# 标题\n正文B\n", encoding="utf-8")

    def fake_build_tree(md_path, model):
        return {"doc_name": "同名文档", "doc_description": "d", "line_count": 2,
                "structure": [{"title": "标题", "node_id": "0001", "line_num": 1,
                               "summary": "s", "text": "t", "nodes": []}]}

    monkeypatch.setattr(run_mod, "build_tree", fake_build_tree)
    out = tmp_path / "data"
    stats = run_mod.ingest_dir(str(md_dir), out, model="fake")

    assert stats["docs"] == 2
    metas = json.loads((out / "workspace" / "_meta.json").read_text(encoding="utf-8"))
    assert len(metas) == 2  # 两个不同 doc_id，未互相覆盖
    ws_files = sorted(p.name for p in (out / "workspace").glob("doc_*.json"))
    assert len(ws_files) == 2

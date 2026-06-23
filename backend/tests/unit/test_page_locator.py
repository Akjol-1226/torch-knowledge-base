import json

from app.core.docparse.convert import _build_page_map
from app.modules.ingest.page_locator import annotate_pages


def test_build_page_map_runs():
    # 行口径与 extract_nodes_from_markdown 一致：剥离标记后按 '\n' 计行
    marked = "a\n<!-- page: 1 -->\nb\n<!-- page: 2 -->\nc"
    # 'a'->行1(默认页1) ; 'b'->行2(页1) ; 'c'->行3(页2)
    assert _build_page_map(marked) == [[1, 1], [3, 2]]


def test_annotate_pages_assigns_from_sidecar(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("x\ny\nz\nw", encoding="utf-8")
    (tmp_path / "doc.md.pagemap.json").write_text(
        json.dumps([[1, 1], [3, 5]]), encoding="utf-8"  # 行1-2→页1，行3起→页5
    )
    structure = [
        {"line_num": 1, "nodes": [{"line_num": 2, "nodes": []}]},
        {"line_num": 4, "nodes": []},
    ]
    n = annotate_pages(md, structure)
    assert n == 3
    assert structure[0]["page"] == 1
    assert structure[0]["nodes"][0]["page"] == 1
    assert structure[1]["page"] == 5  # 行4 落在断点 [3,5] 之后


def test_annotate_pages_no_sidecar_noop(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("x", encoding="utf-8")
    structure = [{"line_num": 1, "nodes": []}]
    assert annotate_pages(md, structure) == 0
    assert "page" not in structure[0]  # 无侧车不改 structure

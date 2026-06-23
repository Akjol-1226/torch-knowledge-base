import json

from app.core.retrieval.treestore import TreeStore


def _make_data(tmp_path):
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    doc = {"id": "doc_a", "type": "md", "path": "x.md",
           "doc_name": "工艺文件", "doc_description": "工艺说明",
           "line_count": 20,
           "structure": [
               {"title": "5 SMT", "node_id": "0001", "line_num": 1, "summary": "贴片回流",
                "text": "本章工艺", "nodes": [
                   {"title": "5.1 印刷", "node_id": "0002", "line_num": 3, "summary": "锡膏",
                    "text": "印刷正文", "nodes": []},
                   {"title": "5.3 回流焊", "node_id": "0003", "line_num": 8, "summary": "回流曲线",
                    "text": "峰值 245℃", "nodes": []}]},
               {"title": "6 检验", "node_id": "0004", "line_num": 15, "summary": "检验项目",
                "text": "外观检验", "nodes": []}]}
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (ws / "_meta.json").write_text(json.dumps({"doc_a": {"doc_name": "工艺文件"}}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_a", "doc_name": "工艺文件", "doc_description": "工艺说明"}], ensure_ascii=False),
        encoding="utf-8")
    return tmp_path


def test_list_catalog(tmp_path):
    ts = TreeStore(_make_data(tmp_path))
    cat = ts.list_catalog()
    assert cat[0]["doc_id"] == "doc_a" and cat[0]["doc_name"] == "工艺文件"


def test_get_outline_top_level_only(tmp_path):
    ts = TreeStore(_make_data(tmp_path))
    out = ts.get_outline("doc_a")
    ids = [n["id"] for n in out["nodes"]]
    assert ids == ["doc_a:0001", "doc_a:0004"]      # 只有顶层
    assert out["nodes"][0]["has_children"] is True


def test_open_node_children(tmp_path):
    ts = TreeStore(_make_data(tmp_path))
    ch = ts.open_node("doc_a:0001")
    assert [c["id"] for c in ch["children"]] == ["doc_a:0002", "doc_a:0003"]


def _make_windowed_data(tmp_path):
    """一个长工序被切成 3 个连续同名兄弟节点（窗口），外加前后不同标题的节点。"""
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    doc = {"id": "doc_w", "type": "md", "path": "x.md",
           "doc_name": "工艺文件", "doc_description": "说明", "line_count": 100,
           "structure": [
               {"title": "G08 前工序", "node_id": "0010", "line_num": 1, "summary": "", "text": "前", "nodes": []},
               {"title": "G09 脱脂工序", "node_id": "0011", "line_num": 10, "summary": "", "text": "移入、装钵", "nodes": []},
               {"title": "G09 脱脂工序", "node_id": "0012", "line_num": 20, "summary": "", "text": "装炉、脱脂", "nodes": []},
               {"title": "G09 脱脂工序", "node_id": "0013", "line_num": 30, "summary": "", "text": "卸炉、移交", "nodes": []},
               {"title": "G10 后工序", "node_id": "0014", "line_num": 40, "summary": "", "text": "后", "nodes": []}]}
    (ws / "doc_w.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (ws / "_meta.json").write_text(json.dumps({"doc_w": {}}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_w", "doc_name": "工艺文件", "doc_description": "说明"}], ensure_ascii=False),
        encoding="utf-8")
    return tmp_path


def test_read_node_exposes_section_span_for_windowed_section(tmp_path):
    ts = TreeStore(_make_windowed_data(tmp_path))
    r = ts.read_node("doc_w:0012")          # 中段
    assert r["section"]["part"] == 2
    assert r["section"]["total"] == 3
    assert r["section"]["span"] == ["doc_w:0011", "doc_w:0012", "doc_w:0013"]


def test_read_node_no_section_for_standalone_node(tmp_path):
    ts = TreeStore(_make_windowed_data(tmp_path))
    r = ts.read_node("doc_w:0010")          # 标题独一无二
    assert "section" not in r


def _make_section_with_children(tmp_path):
    """两个同名窗口，各自挂一个附表子节点（参数表）。span 必须把子节点也带上。"""
    ws = tmp_path / "workspace"; ws.mkdir(parents=True)
    (tmp_path / "catalog").mkdir(parents=True)
    doc = {"id": "doc_c", "doc_name": "工艺文件", "doc_description": "x", "line_count": 100,
           "structure": [
               {"title": "G10 烧结", "node_id": "0020", "line_num": 10, "summary": "", "text": "窗口1",
                "nodes": [{"title": "附表1 烧结曲线", "node_id": "0021", "line_num": 12, "summary": "", "text": "曲线参数", "nodes": []}]},
               {"title": "G10 烧结", "node_id": "0022", "line_num": 20, "summary": "", "text": "窗口2",
                "nodes": [{"title": "附表2 设备", "node_id": "0023", "line_num": 22, "summary": "", "text": "设备表", "nodes": []}]},
               # 同名占位标题"附表1"出现两次但是是【通用占位】，不应被并成一段
               {"title": "附表1", "node_id": "0030", "line_num": 40, "summary": "", "text": "A", "nodes": []},
               {"title": "附表1", "node_id": "0031", "line_num": 50, "summary": "", "text": "B", "nodes": []}]}
    (ws / "doc_c.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_c", "doc_name": "工艺文件", "doc_description": "x"}], ensure_ascii=False),
        encoding="utf-8")
    return tmp_path


def test_section_span_includes_child_nodes(tmp_path):
    ts = TreeStore(_make_section_with_children(tmp_path))
    r = ts.read_node("doc_c:0020")
    assert r["section"]["total"] == 2                       # 2 个窗口
    # span 必须含两个窗口 + 两个附表子节点，按文档序
    assert r["section"]["span"] == ["doc_c:0020", "doc_c:0021", "doc_c:0022", "doc_c:0023"]


def test_generic_placeholder_title_not_merged(tmp_path):
    ts = TreeStore(_make_section_with_children(tmp_path))
    r = ts.read_node("doc_c:0030")                          # 标题"附表1"是通用占位
    assert "section" not in r                               # 不应与 0031 并成一段


def test_read_node_breadcrumb_and_cite(tmp_path):
    ts = TreeStore(_make_data(tmp_path))
    r = ts.read_node("doc_a:0003")
    assert r["text"] == "峰值 245℃"
    assert r["parent_id"] == "doc_a:0001"
    assert r["prev_id"] == "doc_a:0002"
    assert r["path"] == "工艺文件 > 5 SMT > 5.3 回流焊"
    assert r["cite"]["doc"] == "工艺文件"
    assert r["cite"]["section"] == "5.3 回流焊"
    # 行范围：0003 起于 8，下一个文档序节点 0004 起于 15 → 8-14
    assert r["cite"]["lines"] == "8-14"
    # 引用锚点字段：handle（全局唯一）/ doc_id（分组用）/ snippet（高亮用）
    assert r["cite"]["handle"] == "doc_a:0003"
    assert r["cite"]["doc_id"] == "doc_a"
    assert "峰值" in r["cite"]["snippet"]

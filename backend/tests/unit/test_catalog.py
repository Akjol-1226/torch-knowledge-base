from app.modules.ingest import catalog


def test_make_doc_id_stable_and_ascii():
    a = catalog.make_doc_id("NPD9001工艺文件")
    b = catalog.make_doc_id("NPD9001工艺文件")
    assert a == b and a.startswith("doc_") and a.isascii()


def test_build_card_only_id_name_description(tmp_path):
    tree = {"doc_name": "NPD9001工艺文件", "doc_description": "工艺流程说明", "structure": []}
    text = "本工艺文件 NPD9001，回流焊，型号 DEMO1-0603-2X1-50V-0.10n。"
    card = catalog.build_card("doc_x", tree, text, tmp_path / "d.txt")
    # 地图卡片只留这三个字段，不含型号/项目号/类型等噪声
    assert set(card.keys()) == {"doc_id", "doc_name", "doc_description"}
    assert card["doc_description"] == "工艺流程说明"


def test_identifiers_still_written_to_domain_dict(tmp_path):
    # 卡片不放型号，但型号仍应进 domain_dict（供 BM25 认整词）
    tree = {"doc_name": "x", "doc_description": "", "structure": []}
    catalog.build_card("doc_y", tree, "型号 DEMO1-0603-2X1-50V-0.10n 项目 NPD9001", tmp_path / "d.txt")
    dict_text = (tmp_path / "d.txt").read_text(encoding="utf-8")
    assert "DEMO1-0603-2X1-50V-0.10n" in dict_text
    assert "NPD9001" in dict_text

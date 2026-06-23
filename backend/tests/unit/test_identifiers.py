from app.modules.ingest.identifiers import extract_identifiers


def test_extract_project_and_report():
    ids = extract_identifiers("本项目 NPD9001 的鉴定报告 HJ900001 见附录")
    assert "NPD9001" in ids["project_ids"]
    assert "HJ900001" in ids["report_ids"]


def test_product_model_with_dot_not_truncated():
    # 关键：含小数点的型号必须整体抽出，不能被截成 "...0."
    ids = extract_identifiers("型号 DEMO1-0603-2X1-50V-0.10n 的耐压")
    assert "DEMO1-0603-2X1-50V-0.10n" in ids["product_models"]


def test_extract_codes_glued_to_chinese():
    ids = extract_identifiers("本产品型号DEMO1-0603-2X1-50V-0.10n，详见鉴定报告HJ900001，项目NPD9001")
    assert "NPD9001" in ids["project_ids"]
    assert "HJ900001" in ids["report_ids"]
    assert "DEMO1-0603-2X1-50V-0.10n" in ids["product_models"]

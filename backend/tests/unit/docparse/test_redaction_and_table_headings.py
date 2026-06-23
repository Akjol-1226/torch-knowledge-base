from app.core.docparse.heading_rules import (
    appendix_table_level,
    heading_level_for_title,
    is_redaction_notsure_inner,
)
from app.core.docparse.models import Heading
from app.core.docparse.postprocess import (
    demote_figure_formula_headings,
    normalize_redacted_notsure,
    promote_table_title_headings,
)
from app.core.docparse.structure_enrich import (
    align_process_headings_to_page,
    extract_table_title_headings,
)


def test_redaction_notsure_becomes_empty():
    inp = "| 1 | <NOTSURE>/////</NOTSURE> | <NOTSURE>被涂抹</NOTSURE> |"
    out = normalize_redacted_notsure(inp)
    assert "<NOTSURE>" not in out
    assert "被涂抹" not in out
    assert "| 1 |  |  |" in out or "| 1 | | |" in out.replace(" ", "")


def test_ocr_notsure_preserved():
    inp = "产值<NOTSURE>增长</NOTSURE>了三成"
    out = normalize_redacted_notsure(inp)
    assert "<NOTSURE>增长</NOTSURE>" in out


def test_strips_xxx_redaction_placeholder():
    inp = "选<NOTSURE>XXX</NOTSURE>瓷粉，内电极"
    out = normalize_redacted_notsure(inp)
    assert "<NOTSURE>" not in out
    assert "XXX" not in out
    assert "选瓷粉" in out


def test_strips_long_x_placeholder_block():
    inp = "瓷粉<NOTSURE>XXXXXX XXXX XXXX XXXX XXXX</NOTSURE>匹配"
    out = normalize_redacted_notsure(inp)
    assert "<NOTSURE>" not in out
    assert "XXXX" not in out
    assert "瓷粉匹配" in out


def test_strips_signature_field_notsure():
    inp = "填表/日期：<u><NOTSURE>黄晓红</NOTSURE> 2023.12.31</u>"
    out = normalize_redacted_notsure(inp)
    assert "<NOTSURE>" not in out
    assert "黄晓红" not in out
    assert "2023.12.31" in out


def test_strips_bare_xxx_in_parentheses():
    inp = "#### 表3 CTK41B 型多层片式瓷介固定电容器(XXX瓷粉)技术指标"
    out = normalize_redacted_notsure(inp)
    assert "XXX" not in out
    assert "(瓷粉)" in out
    assert is_redaction_notsure_inner("XXX")
    assert is_redaction_notsure_inner("XXXXXX XXXX XXXX")
    assert not is_redaction_notsure_inner("增长")
    assert not is_redaction_notsure_inner("供")


def test_malformed_notsur_closed_or_stripped():
    inp = "**设备**: <NOTSURE>...</NOTSUR>"
    out = normalize_redacted_notsure(inp)
    assert "NOTSUR" not in out


def test_appendix_level_under_section():
    assert heading_level_for_title("附表1 黏剂配方工艺", section_level=3) == 3
    assert heading_level_for_title("表1-1 主要指标", section_level=3) == 4
    assert heading_level_for_title("表1-1 主要指标", section_level=3, appendix_level=3) == 4
    assert heading_level_for_title("图2-3 趋势") is None


def test_promote_appendix_under_g01_section():
    inp = (
        "### 火炬电子 G01 配料工序工艺规程（关键工序）\n\n"
        "**附表1 黏剂配方工艺**\n\n"
        "**表1-1 主要财务指标**\n| a |"
    )
    out = promote_table_title_headings(inp)
    assert "### 附表1 黏剂配方工艺" in out
    assert "#### 表1-1 主要财务指标" in out


def test_demote_figure_only():
    inp = "### 图2-3 销售趋势图\n#### 表1-1 指标"
    out = demote_figure_formula_headings(inp)
    assert "**图2-3 销售趋势图**" in out
    assert "#### 表1-1 指标" in out


def test_extract_appendix_level_from_section():
    raw = "**附表2 第一阶段瓷浆配方表**\n| 序号 |"
    heads = extract_table_title_headings(raw, section_level=3)
    assert any(h.level == 3 and "附表2" in h.text for h in heads)


def test_align_process_heading_uses_full_line():
    known = [Heading(text="G01 配料工序工艺规程（关键工序）", level=3, type="body_heading")]
    raw = "火炬电子 G01 配料工序工艺规程（关键工序） 产品名称"
    aligned = align_process_headings_to_page(raw, known)
    assert any("火炬电子" in h.text for h in aligned)


def test_appendix_table_level_helper():
    assert appendix_table_level(2) == 2
    assert appendix_table_level(3) == 3
    assert appendix_table_level(6) == 6

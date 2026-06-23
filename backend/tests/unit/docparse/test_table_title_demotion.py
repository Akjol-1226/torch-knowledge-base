from app.core.docparse.postprocess import (
    demote_figure_formula_headings,
    promote_table_title_headings,
)


def test_promotes_plain_table_under_section():
    inp = "### 1.1 小节\n\n**表1-1 主要财务指标**\n| A | B |"
    out = promote_table_title_headings(inp)
    assert "#### 表1-1 主要财务指标" in out


def test_promotes_bold_table_to_heading():
    inp = "**表1-1 主要财务指标**\n| A | B |"
    out = promote_table_title_headings(inp)
    assert "### 表1-1 主要财务指标" in out
    assert "**表1-1" not in out


def test_demotes_figure_heading():
    inp = "### 图2-3 销售趋势图"
    out = demote_figure_formula_headings(inp)
    assert "**图2-3 销售趋势图**" in out


def test_promotes_appendix_under_parent():
    inp = "### G01 配料工序工艺规程\n\n**附表1 黏剂配方工艺**\n| 序号 |"
    out = promote_table_title_headings(inp)
    assert "### 附表1 黏剂配方工艺" in out


def test_preserves_appendix_table_heading_with_colon():
    inp = "### 章节\n\n#### 附表1：项目明细表"
    out = promote_table_title_headings(inp)
    assert "### 附表1：项目明细表" in out


def test_appendix_not_deepened_by_flowchart_h4():
    inp = (
        "### G01 配料工序工艺规程\n"
        "#### 流程图/架构图信息\n"
        "#### 附表1 黏剂配方工艺\n"
    )
    out = promote_table_title_headings(inp)
    assert "### 附表1 黏剂配方工艺" in out
    assert "#### 附表1" not in out

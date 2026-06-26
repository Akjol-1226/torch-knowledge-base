from app.core.docparse.postprocess import demote_figure_formula_headings


def test_demotes_figure_heading():
    inp = "### 图2-3 销售趋势图"
    out = demote_figure_formula_headings(inp)
    assert "**图2-3 销售趋势图**" in out


def test_table_heading_not_demoted_by_figure_pass():
    """表题不属于图/公式，demote_figure_formula_headings 不得碰它。"""
    inp = "#### 表1-1 指标"
    out = demote_figure_formula_headings(inp)
    assert "#### 表1-1 指标" in out

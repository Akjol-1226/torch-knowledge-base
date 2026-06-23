from app.core.docparse.postprocess import fix_flowchart_page_titles


def test_process_regulation_at_end_of_page_no_index_error():
    """工艺规程加粗行出现在页末尾，后面全是空行，不应抛 IndexError。"""
    # 加粗的工艺规程行在最后，后续只有空行——_skip_blank_lines 会返回 len(lines)
    inp = "**焊接工艺规程**\n\n\n"
    # 不抛异常即通过
    out = fix_flowchart_page_titles(inp)
    assert "焊接工艺规程" in out


def test_process_regulation_at_very_last_line_no_index_error():
    """工艺规程加粗行是文档的最后一行（无尾随换行），不应抛 IndexError。"""
    inp = "**装配工艺规程**"
    out = fix_flowchart_page_titles(inp)
    assert "装配工艺规程" in out


def test_process_regulation_followed_by_flowchart_label_merges():
    """工艺规程行后紧跟 **流程图**，应合并为 ### 标题。"""
    inp = "**焊接工艺规程**\n**流程图**\n"
    out = fix_flowchart_page_titles(inp)
    assert "### 焊接工艺规程 流程图" in out


def test_process_regulation_followed_by_blank_then_flowchart_label():
    """工艺规程行与 **流程图** 之间有空行，仍应正确合并。"""
    inp = "**焊接工艺规程**\n\n**流程图**\n"
    out = fix_flowchart_page_titles(inp)
    assert "### 焊接工艺规程 流程图" in out


def test_process_regulation_without_flowchart_label_left_unchanged():
    """工艺规程行后没有 **流程图**，行内容保持原样。"""
    inp = "**焊接工艺规程**\n一些正文内容\n"
    out = fix_flowchart_page_titles(inp)
    assert "**焊接工艺规程**" in out

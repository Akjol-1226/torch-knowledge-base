from app.core.docparse.postprocess import repair_unclosed_html_tables


# -- Task 1: repair_unclosed_html_tables ---------------------------------------


def test_repair_adds_missing_table_close():
    text = "<!-- page: 1 -->\n<table>\n<tbody>\n<tr><td>A</td></tr>\n<!-- page: 2 -->\n正文"
    out = repair_unclosed_html_tables(text)
    assert "</table>" in out
    # The closing tag must appear before the next page marker
    table_close_pos = out.index("</table>")
    page2_pos = out.index("<!-- page: 2 -->")
    assert table_close_pos < page2_pos


def test_repair_adds_tbody_and_table_close():
    text = "<!-- page: 1 -->\n<table>\n<tbody>\n<tr><td>A</td></tr>\n<!-- page: 2 -->\n正文"
    out = repair_unclosed_html_tables(text)
    assert "</tbody>" in out
    assert "</table>" in out


def test_repair_leaves_closed_table_unchanged():
    text = "<!-- page: 1 -->\n<table>\n<tbody>\n<tr><td>A</td></tr>\n</tbody>\n</table>\n正文"
    out = repair_unclosed_html_tables(text)
    assert out.count("</table>") == 1


def test_repair_handles_no_tables():
    text = "<!-- page: 1 -->\n正文内容\n<!-- page: 2 -->\n更多内容"
    out = repair_unclosed_html_tables(text)
    assert out == text


def test_repair_handles_text_without_page_markers():
    text = "<table>\n<tbody>\n<tr><td>A</td></tr>\n"
    out = repair_unclosed_html_tables(text)
    assert "</table>" in out

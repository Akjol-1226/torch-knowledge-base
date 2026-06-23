from app.core.docparse.postprocess import demote_headings_in_html_table_cells


def test_strips_heading_inside_td():
    inp = "<td>\n## 3、产品标准是否明确（☑是，□否）；<br>\n## 4、工艺要求是否明确（☑是，□否）；<br>\n</td>"
    out = demote_headings_in_html_table_cells(inp)
    assert "## 3、" not in out
    assert "## 4、" not in out
    assert "3、产品标准是否明确（☑是，□否）；<br>" in out
    assert "4、工艺要求是否明确（☑是，□否）；<br>" in out


def test_preserves_heading_outside_td():
    inp = "## 项目输入评审记录\n<td>\n## 3、清单项；<br>\n</td>"
    out = demote_headings_in_html_table_cells(inp)
    assert inp.splitlines()[0] in out   # 第一行 ## 标题保留
    assert "## 3、" not in out


def test_preserves_heading_in_code_block():
    inp = "```\n## not a real heading\n```"
    out = demote_headings_in_html_table_cells(inp)
    assert out == inp


def test_td_on_same_line_as_content():
    inp = "<td>## 3、清单项；</td>"
    out = demote_headings_in_html_table_cells(inp)
    assert "## 3、" not in out


def test_heading_restored_after_td_close():
    inp = "<td>\n## 清单项；<br>\n</td>\n## 正常章节标题"
    out = demote_headings_in_html_table_cells(inp)
    assert "## 清单项" not in out
    assert "## 正常章节标题" in out


from app.core.docparse.postprocess import demote_semicolon_sentence_headings


def test_demotes_numbered_heading_ending_in_semicolon():
    inp = "### 2、温度加速常数 θ 为 10（元器件寿命 10°C法则）；"
    out = demote_semicolon_sentence_headings(inp)
    assert not out.strip().startswith("#")
    assert "2、温度加速常数" in out


def test_demotes_dotted_heading_ending_in_semicolon():
    inp = "#### 1.1 高可靠等级产品的瓷浆分散；"
    out = demote_semicolon_sentence_headings(inp)
    assert not out.strip().startswith("#")


def test_preserves_heading_not_ending_in_semicolon():
    inp = "### 1.1 产品信息来源"
    out = demote_semicolon_sentence_headings(inp)
    assert out == inp


def test_preserves_process_step_ending_in_period():
    inp = "## 1、内电极储存：内电极平时需储存在冰箱内，冰箱允许温度范围 5-15℃。"
    out = demote_semicolon_sentence_headings(inp)
    assert out == inp


def test_demote_semicolon_preserves_in_code_block():
    inp = "```\n### 3、某清单项；\n```"
    out = demote_semicolon_sentence_headings(inp)
    assert out == inp


from app.core.docparse.postprocess import demote_toc_style_headings


def test_demotes_run_of_dotted_toc_entries():
    inp = (
        "## 一、 目的 .............................................1\n"
        "## 二、 产品适用范围 ......................................1\n"
        "## 三、 适用法律法规及标准 ................................1\n"
        "## 四、 职责 ..............................................2\n"
    )
    out = demote_toc_style_headings(inp)
    assert "## 一、" not in out
    assert "## 二、" not in out
    assert "## 三、" not in out
    assert "## 四、" not in out
    # Should be converted to list items
    assert "- 一、 目的" in out


def test_preserves_short_run_below_threshold():
    # Only 2 consecutive dotted entries — below threshold of 3, keep as-is
    inp = (
        "## 一、 目的 ............1\n"
        "## 二、 产品适用范围 ...1\n"
        "## 正文章节\n"
    )
    out = demote_toc_style_headings(inp)
    assert "## 一、" in out
    assert "## 二、" in out


def test_preserves_normal_h2_not_dotted():
    inp = "## 项目输入评审记录\n## 项目立项申请书\n## CTK41B型多层片式瓷介固定电容器\n"
    out = demote_toc_style_headings(inp)
    assert out == inp


def test_preserves_in_code_block():
    inp = "```\n## 一、目的 .........1\n## 二、范围 .........1\n## 三、内容 .........1\n```"
    out = demote_toc_style_headings(inp)
    assert out == inp

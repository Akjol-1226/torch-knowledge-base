from app.core.docparse.convert_page import (
    _has_process_regulation_heading,
    _page_has_figure_content,
    build_page_context,
    diagram_descriptions_incomplete,
    figure_captions_missing_descriptions,
    process_table_diagram_issues,
    visual_descriptions_incomplete,
)
from app.core.docparse.models import DocumentContext, Heading, PageRegion, PageStructure


def test_detects_image_html_placeholder():
    md = "| 原料 | <!-- Image (Process Flowchart) --> | 1.1 步骤 |"
    assert 'image_html_placeholder' in diagram_descriptions_incomplete(md)


def test_passes_when_description_present():
    md = (
        "| 流程 | 图示 | **工序（步）内容及要求** |\n"
        "| a | *(图片内容描述：流程图自上而下…)* | 1.1 步骤 |"
    )
    assert diagram_descriptions_incomplete(md) == []


def test_detects_missing_description_on_process_table():
    md = (
        "| 流程 | 图示 | **工序（步）内容及要求** | 设备 |\n"
        "| 原料称取 | <!-- Image --> | 1.1 步骤 | 天平 |"
    )
    issues = diagram_descriptions_incomplete(md)
    assert 'image_html_placeholder' in issues


def test_detects_missing_diagram_on_second_step_row():
    md = (
        "| 流程 | 图示 | 工序（步）内容及要求 | 设备 |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| 原料称取 | *(图片内容描述：天平称量)* | 1.1 步骤 | 天平 |\n"
        "| 悬混制作 | | 2.1 步骤 | 搅拌机 |"
    )
    issues = process_table_diagram_issues(md)
    assert 'missing_process_table_diagram' in issues


def test_detects_missing_diagram_on_html_process_table():
    md = (
        "<table>\n"
        "  <thead>\n"
        "    <tr><th>流程</th><th>图示</th><th>工序（步）内容及要求</th><th>设备</th></tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        "    <tr><td>原料称取</td><td>*(图片内容描述：天平称量)*</td><td>1.1 步骤</td><td>天平</td></tr>\n"
        "    <tr><td>悬混制作</td><td></td><td>2.1 步骤</td><td>搅拌机</td></tr>\n"
        "  </tbody>\n"
        "</table>"
    )

    issues = process_table_diagram_issues(md)

    assert 'missing_process_table_diagram' in issues


def test_detects_short_diagram_on_html_process_table():
    md = (
        "<table>\n"
        "  <tr><th>流程</th><th>图示</th><th>工序（步）内容及要求</th></tr>\n"
        "  <tr><td>原料称取</td><td>设备图</td><td>1.1 步骤</td></tr>\n"
        "</table>"
    )

    issues = process_table_diagram_issues(md)

    assert 'missing_process_table_diagram' in issues


def test_skips_arrow_only_flow_rows():
    md = (
        "| 流程 | 图示 | 工序（步）内容及要求 |\n"
        "| :--- | :--- | :--- |\n"
        "| 原料称取 | *(图片内容描述：设备图)* | 1.1 步骤 |\n"
        "| ↓ | | |"
    )
    assert process_table_diagram_issues(md) == []


def test_detects_missing_figure_caption_description():
    md = "**图3 产品装配图**\n\n### 7.2 下一节"
    assert figure_captions_missing_descriptions(md) == ['missing_figure_caption_description']


def test_passes_figure_caption_with_description():
    md = (
        "**图3 产品装配图**\n"
        "*(图片内容描述：电容器与 PCB 焊盘装配示意)*\n"
        "### 7.2 下一节"
    )
    assert figure_captions_missing_descriptions(md) == []


def test_visual_checks_figure_and_process_independently():
    md = "**图4 曲线**\n"
    issues = visual_descriptions_incomplete(md, check_figure_captions=True, check_process_table=False)
    assert 'missing_figure_caption_description' in issues


def test_has_process_regulation_heading_from_heading():
    known = [Heading(text='G01 配料工序工艺规程（关键工序）', level=3, type='body_heading')]
    assert _has_process_regulation_heading(known, '')


def test_page_has_figure_content_from_region():
    ps = PageStructure(page_no=3, regions=[PageRegion(type='figure', notes='装配图')])
    assert _page_has_figure_content(ps, '')


def test_build_page_context_sets_diagram_flag():
    ctx = DocumentContext(pdf_path='x.pdf', file_title='设计文档', total_pages=1)
    ctx.page_structures[1] = PageStructure(
        page_no=1,
        headings=[Heading(text='G01 配料工序工艺规程（关键工序）', level=3, type='body_heading')],
    )
    pc = build_page_context(ctx, 1, None, page_raw_text='G01 配料工序工艺规程（关键工序）')
    assert pc.require_diagram_descriptions is True
    assert pc.require_figure_descriptions is False
    assert pc.require_flowchart_structure is False


def test_build_page_context_sets_figure_flag():
    ctx = DocumentContext(pdf_path='x.pdf', file_title='测试文档1', total_pages=1)
    ctx.page_structures[1] = PageStructure(
        page_no=1,
        regions=[PageRegion(type='figure', notes='图3 产品装配图')],
    )
    pc = build_page_context(ctx, 1, None, page_raw_text='图3 产品装配图')
    assert pc.require_figure_descriptions is True
    assert pc.require_diagram_descriptions is False

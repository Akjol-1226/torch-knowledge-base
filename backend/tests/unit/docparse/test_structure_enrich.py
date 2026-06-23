from app.core.docparse.convert_page import extract_headings_from_markdown
from app.core.docparse.models import Heading, PageRegion, PageStructure
from app.core.docparse.structure_enrich import (
    enrich_page_structure,
    extract_process_section_headings,
    flowchart_output_complete,
    is_stack_heading,
    match_process_section_for_page,
    page_has_flowchart,
    parse_process_sections_from_toc_text,
)


def test_is_stack_heading_excludes_flowchart_sections():
    assert not is_stack_heading('流程图/架构图信息')
    assert not is_stack_heading('节点列表')
    assert not is_stack_heading('Mermaid 图')
    assert is_stack_heading('G01 配料工序工艺规程')


def test_extract_process_section_from_line():
    text = '火炬电子 G01 配料工序工艺规程（关键工序） 产品名称'
    headings = extract_process_section_headings(text)
    assert any('G01' in h.text and '工艺规程' in h.text for h in headings)
    assert headings[0].level == 3


def test_enrich_clears_table_continuation_on_flowchart_page():
    ps = PageStructure(
        page_no=4,
        is_table_continuation=True,
        headings=[Heading(text='CTK41B型多层片式瓷介固定电容器工艺流程图', level=2, type='body_heading')],
    )
    raw = '火炬电子 CTK41B型多层片式瓷介固定电容器工艺流程图 执行标准 GJB'
    enrich_page_structure(ps, raw)
    assert ps.is_table_continuation is False
    assert page_has_flowchart(ps, raw)


def test_flowchart_output_complete_detects_missing():
    md = '#### 流程图/架构图信息\nxxx\n#### 节点列表\n| a | b |\n'
    missing = flowchart_output_complete(md)
    assert '关系列表' in missing
    assert 'mermaid_codeblock' in missing


def test_flowchart_output_complete_accepts_h4_sections():
    md = (
        '### G04 工艺流程图\n'
        '#### 流程图/架构图信息\nx\n#### 节点列表\n| a | b |\n|---|\n'
        '#### 关系列表\n* A -> B\n#### 流程链路总结\npath\n#### Mermaid 图\n'
        '```mermaid\nflowchart TD\n  A --> B\n```\n'
    )
    assert flowchart_output_complete(md) == []


def test_page_has_flowchart_false_on_toc_page():
    ps = PageStructure(
        page_no=2,
        is_toc_page=True,
        headings=[Heading(text='工艺文件目录', level=2, type='body_heading')],
        regions=[
            PageRegion(type='toc', notes='目录表'),
            PageRegion(type='figure', notes='误判区域'),
        ],
    )
    raw = '| 1 | CTK41B型多层片式瓷介固定电容器工艺生产流程图 | HJ4.603 | |'
    assert not page_has_flowchart(ps, raw)


def test_page_has_flowchart_false_when_only_figure_region():
    ps = PageStructure(
        page_no=5,
        regions=[PageRegion(type='figure', notes='工序图示')],
        headings=[Heading(text='G01 配料工序工艺规程（关键工序）', level=3, type='body_heading')],
    )
    raw = '火炬电子 G01 配料工序工艺规程（关键工序） 产品名称'
    assert not page_has_flowchart(ps, raw)


def test_page_has_flowchart_true_with_keyword_and_figure():
    ps = PageStructure(
        page_no=2,
        regions=[PageRegion(type='figure', notes='流程图')],
        headings=[Heading(text='CTK41B型多层片式瓷介固定电容器工艺流程图', level=2, type='body_heading')],
    )
    raw = '火炬电子 CTK41B型多层片式瓷介固定电容器工艺流程图 执行标准 GJB'
    assert page_has_flowchart(ps, raw)


def test_match_process_section_uses_g_code_not_prefix():
    sections = [
        Heading(text='G01 配料工序工艺规程（关键工序）', level=3, type='body_heading'),
        Heading(text='G02 瓷浆辊轧工艺规程', level=3, type='body_heading'),
    ]
    raw = '本页仅涉及 G02 瓷浆辊轧工艺规程 产品名称'
    matched = match_process_section_for_page(raw, sections)
    texts = [h.text for h in matched]
    assert any('G02' in t for t in texts)
    assert not any('G01' in t for t in texts)


def test_extract_headings_skips_flowchart_template():
    md = """### 流程图/架构图信息
x
### G01 配料工序工艺规程
y
"""
    headings = extract_headings_from_markdown(md)
    texts = [h.text for h in headings]
    assert '流程图/架构图信息' not in texts
    assert 'G01 配料工序工艺规程' in texts


def test_parse_toc_process_sections():
    text = '| 2 | G01 配料工序工艺规程（关键工序） | HJ4.603 | |\n| 3 | G02 瓷浆辊轧工艺规程 | HJ4.603 | |'
    sections = parse_process_sections_from_toc_text(text)
    assert len(sections) >= 2

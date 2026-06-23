from app.core.docparse.models import DocumentContext
from app.core.docparse.postprocess import (
    demote_list_items_in_flowchart_sections,
    fix_flowchart_page_titles,
    fix_numbered_heading_levels,
    postprocess_markdown,
    promote_plain_numbered_headings,
    relevel_headings_under_chinese_sections,
)


def _postprocess_body(body: str, title: str = '测试') -> str:
    ctx = DocumentContext(pdf_path='x.pdf', file_title=title, total_pages=1)
    return postprocess_markdown(f'<!-- page: 1 -->{body}', title, ctx)


def test_promote_dunhao_and_dot_subsections():
    inp = """## 2. 现有基础及技术继承性分析
产品在整体结构方面与以往电容器产品相同。

3、产品标准及法律法规执行情况
3.1 执行 GJB92B-2011《规范》。
3.2 适用法律法规

## 4. 工艺要求
"""
    out = fix_numbered_heading_levels(promote_plain_numbered_headings(inp))
    assert "## 3、产品标准及法律法规执行情况" in out
    assert "### 3.1 执行 GJB92B-2011《规范》。" in out
    assert "### 3.2 适用法律法规" in out
    assert "## 4. 工艺要求" in out


def test_does_not_promote_figure_bold_lines():
    inp = "**图3 产品装配简图**"
    assert promote_plain_numbered_headings(inp) == inp


def test_chinese_major_with_arabic_subsections():
    inp = """## 一、立项必要性分析

## 1. 背景：
正文

## 2. 市场前景：

## 二、产品简介

## 1. 产品范围
1.1. 拟立项产品规格型号
列表

1.2. 项目覆盖范围（尺寸、电压、容量、质量等级等）
"""
    out = relevel_headings_under_chinese_sections(
        fix_numbered_heading_levels(promote_plain_numbered_headings(inp))
    )
    assert '## 一、立项必要性分析' in out
    assert '### 1. 背景：' in out
    assert '### 2. 市场前景：' in out
    assert '## 二、产品简介' in out
    assert '### 1. 产品范围' in out
    assert '#### 1.1. 拟立项产品规格型号' in out
    assert '#### 1.2. 项目覆盖范围' in out


def test_demote_numbered_list_inside_flowchart_summary():
    inp = """#### 流程链路总结
本页内容非线性流程：
## 1. **输入条件**：确定焊接方式。
## 2. **查表路径 A**：查阅载流焊表。
"""
    out = demote_list_items_in_flowchart_sections(inp)
    assert '## 1.' not in out
    assert '1. **输入条件**：确定焊接方式。' in out
    assert '2. **查表路径 A**：查阅载流焊表。' in out


def test_fix_flowchart_page_title_from_meta_heading():
    inp = """### 工艺流程图

**火炬电子 CTK41B型多层片式瓷介固定电容器工艺规程**
**流程图**

执行标准：GJB 1928-2011
"""
    out = fix_flowchart_page_titles(inp)
    assert '### 工艺流程图' not in out
    assert '### 火炬电子 CTK41B型多层片式瓷介固定电容器工艺规程 流程图' in out
    assert '**流程图**' not in out

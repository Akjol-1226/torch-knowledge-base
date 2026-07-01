# tests/test_prompt_table_rules.py
from app.core.docparse.prompts import PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_html_table_instruction():
    """Table rules must mention HTML <table>."""
    assert "<table>" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_colspan_rowspan():
    """New rules must mention colspan and rowspan."""
    assert "colspan" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "rowspan" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_requires_all_tables_as_html():
    """All document tables should be emitted as HTML tables."""
    assert "PDF 原文中的文档表格" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "一律使用 HTML" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_forbids_markdown_tables():
    """Document tables should not use Markdown pipe table syntax."""
    assert "严禁" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "Markdown `|` 表格语法输出 PDF 原文中的文档表格" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_allows_flowchart_helper_structures():
    """Flowchart Mermaid and generated helper lists are not document tables."""
    assert "Mermaid 图" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "节点列表" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "生成型辅助结构" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_old_flat_table_rule_removed():
    """The old single-line rule must be gone."""
    assert "用标准 Markdown 表格语法完整还原所有行列" not in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_no_ghost_cells_rule():
    """Prompt must say merged-away cells are not output."""
    assert "被合并掉的单元格" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_no_style_attribute_rule():
    """Prompt must explicitly prohibit style/class/id attributes on HTML tables."""
    assert "不添加任何" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "style" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "class" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "id" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_column_mismatch_failure_example():
    """Prompt must include a failure example showing column-count mismatch."""
    assert "列数不一致" in PAGE_MARKDOWN_SYSTEM_PROMPT

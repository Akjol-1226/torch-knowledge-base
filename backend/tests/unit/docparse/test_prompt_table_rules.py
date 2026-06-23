# tests/test_prompt_table_rules.py
from app.core.docparse.prompts import PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_html_table_instruction():
    """New rules must mention HTML <table> for merged cells."""
    assert "<table>" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_colspan_rowspan():
    """New rules must mention colspan and rowspan."""
    assert "colspan" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "rowspan" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_two_path_rule():
    """New rules must distinguish simple vs complex tables."""
    assert "简单表格" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "复杂表格" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_markdown_table_requires_leading_pipe():
    """New rules must require leading | on every row."""
    assert "每行必须以 `|` 开头" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_old_flat_table_rule_removed():
    """The old single-line rule must be gone."""
    assert "用标准 Markdown 表格语法完整还原所有行列" not in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_separator_row_rule():
    """Prompt must specify that separator row column count matches header."""
    assert "列数必须与表头完全一致" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_no_ghost_cells_rule():
    """Prompt must say merged-away cells are not output."""
    assert "被合并掉的单元格" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_no_style_attribute_rule():
    """Prompt must explicitly prohibit style/class/id attributes on HTML tables."""
    assert "不添加任何" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "style" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "class" in PAGE_MARKDOWN_SYSTEM_PROMPT
    assert "id" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_data_row_column_count_rule():
    """Prompt must require data rows to have the same column count as the header."""
    assert "所有数据行的列数必须与表头列数完全相同" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_forbids_markdown_for_merged_cells():
    """Prompt must explicitly forbid Markdown | for merged-cell tables."""
    assert "严禁" in PAGE_MARKDOWN_SYSTEM_PROMPT


def test_prompt_contains_column_mismatch_failure_example():
    """Prompt must include a failure example showing column-count mismatch."""
    assert "列数不一致" in PAGE_MARKDOWN_SYSTEM_PROMPT

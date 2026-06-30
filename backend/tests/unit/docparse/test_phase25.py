"""Tests for Phase 2.5: targeted cross-page table-split repair (no live LLM calls).

[torch 改动] 就地修列（续表行留在原页、不搬页、不插标记），故端到端测试断言的是"续表行在
它原本那一页被对齐成参照列数、且不再有被误当表头的行"，而非上游的"行被搬到上一页"。
"""
from unittest.mock import patch

from app.core.docparse import phase25
from app.core.docparse.phase25 import (
    _col_count,
    _faithful_rows,
    _is_separator_row,
    _leading_table_block,
    _parse_repaired_rows,
    _trailing_table_lines,
    repair_format_with_llm,
)

# ── low-level helpers ─────────────────────────────────────────────────────────


def test_col_count_counts_cells():
    assert _col_count(['| a | b | c | d |']) == 4
    assert _col_count(['| 23 | 首件鉴定目录 | TE-QR-G1247 |']) == 3


def test_trailing_table_lines_grabs_table_at_end():
    body = "项目名称：X\n\n| 序号 | 名称 | 备注 |\n| :--- | :--- | :--- |\n| 1 | a | |\n| 2 | b | |"
    ref = _trailing_table_lines(body)
    assert ref is not None
    assert ref[0].startswith('| 序号')
    assert ref[-1].startswith('| 2')


def test_trailing_table_lines_none_when_text_at_end():
    assert _trailing_table_lines("| 1 | a |\n收尾说明文字") is None


def test_leading_table_block_when_page_starts_with_table():
    body = "| 23 | 首件鉴定目录 | TE-QR-G1247 |\n| :--- | :--- | :--- |\n| 24 | x | y |\n\n后续文字"
    res = _leading_table_block(body)
    assert res is not None
    frag, remaining = res
    assert frag[0].startswith('| 23')
    assert '后续文字' in remaining
    assert '首件鉴定目录' not in remaining


def test_leading_table_block_none_when_page_starts_with_heading():
    assert _leading_table_block("## 新章节\n\n| a | b |\n| :- | :- |\n| 1 | 2 |") is None


def test_parse_repaired_rows_strips_fences_and_separators():
    out = "```\n| 23 | 首件鉴定目录 | TE-QR-G1247 | |\n| :--- | :--- | :--- | :--- |\n| 24 | x | y | |\n```"
    rows = _parse_repaired_rows(out)
    assert rows == ['| 23 | 首件鉴定目录 | TE-QR-G1247 | |', '| 24 | x | y | |']


def test_parse_repaired_rows_not_continuation():
    assert _parse_repaired_rows('NOT_CONTINUATION') is None


def test_faithful_true_when_only_empty_cols_added():
    frag = ['| 23 | 首件鉴定目录 | TE-QR-G1247 |']
    rows = ['| 23 | 首件鉴定目录 | TE-QR-G1247 | |']  # added empty 备注 col
    assert _faithful_rows(rows, frag) is True


def test_faithful_false_when_invented_text():
    frag = ['| 23 | 首件鉴定目录 | TE-QR-G1247 |']
    rows = ['| 23 | 凭空捏造 | TE-QR-G1247 | |']
    assert _faithful_rows(rows, frag) is False


def test_faithful_false_when_row_dropped():
    # C1: LLM 漏掉一行（如 token 截断）→ 必须拒绝，否则该行内容永久丢失
    frag = ['| 23 | a | x |', '| 24 | b | y |', '| 25 | c | z |']
    rows = ['| 23 | a | x | |', '| 24 | b | y | |']  # 缺第 25 行
    assert _faithful_rows(rows, frag) is False


def test_faithful_false_when_cell_blanked():
    # C1: LLM 把某格清空但列数不变 → 必须拒绝
    frag = ['| 23 | 首件鉴定目录 | TE-QR-G1247 |']
    rows = ['| 23 |  | TE-QR-G1247 | |']
    assert _faithful_rows(rows, frag) is False


def test_faithful_false_when_cells_reordered():
    # M2: 列被调换顺序 → 必须拒绝
    frag = ['| 23 | 首件鉴定目录 | TE-QR-G1247 |']
    rows = ['| 23 | TE-QR-G1247 | 首件鉴定目录 | |']
    assert _faithful_rows(rows, frag) is False


def test_separator_row_excludes_single_dash_data_row():
    assert _is_separator_row('| :--- | :--- | :--- |') is True
    assert _is_separator_row('| --- | --- |') is True
    assert _is_separator_row('| - | - |') is False  # 单破折号是占位数据，不是分隔行
    assert _is_separator_row('| 1 | 2 |') is False


# ── end-to-end splice ─────────────────────────────────────────────────────────

_PREV = (
    "项目名称：X\n\n"
    "| 序号 | 文件名称 | 文件编号 | 备注 |\n"
    "| :--- | :--- | :--- | :--- |\n"
    "| 21 | 样品制造 | TE-QR-G533 | |\n"
    "| 22 | 试制准备 | TE-QR-G1246 | |"
)
_CURR = (
    "| 23 | 首件鉴定目录 | TE-QR-G1247 |\n"
    "| :--- | :--- | :--- |\n"
    "| 24 | 首件生产 | TE-QR-G1249 |"
)


def _raw_two_pages(prev: str, curr: str) -> str:
    return f"<!-- page: 2 -->\n{prev}\n\n<!-- page: 3 -->\n{curr}"


def test_repair_merges_continuation_into_prev_page():
    raw = _raw_two_pages(_PREV, _CURR)
    fixed_rows = "| 23 | 首件鉴定目录 | TE-QR-G1247 | |\n| 24 | 首件生产 | TE-QR-G1249 | |"

    cfg = type('C', (), {'enable_phase25': True})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair', return_value=fixed_rows) as m:
        out = repair_format_with_llm(raw)

    assert m.called
    # 续表行被对齐成 4 列、合并到上一页(page2)表尾 → 跨页表合成一张完整可渲染表
    assert '| 23 | 首件鉴定目录 | TE-QR-G1247 | |' in out
    assert '| 24 | 首件生产 | TE-QR-G1249 | |' in out
    page2 = out.split('<!-- page: 3 -->')[0]
    page3 = out.split('<!-- page: 3 -->')[1]
    assert '| 23 |' in page2 and '| 24 |' in page2              # 续表行已并入 page2 表尾
    assert '| 23 | 首件鉴定目录 | TE-QR-G1247 |\n' not in page3  # page3 不再以那截 3 列表格开头
    assert '<!-- page: 2 -->' in out and '<!-- page: 3 -->' in out


def test_repair_skips_when_not_continuation():
    raw = _raw_two_pages(_PREV, _CURR)
    cfg = type('C', (), {'enable_phase25': True})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair', return_value='NOT_CONTINUATION'):
        out = repair_format_with_llm(raw)
    assert out == raw


def test_repair_skips_when_faithfulness_violated():
    raw = _raw_two_pages(_PREV, _CURR)
    bad = "| 23 | 凭空捏造的名称 | TE-QR-G1247 | |\n| 24 | 首件生产 | TE-QR-G1249 | |"
    cfg = type('C', (), {'enable_phase25': True})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair', return_value=bad):
        out = repair_format_with_llm(raw)
    assert out == raw


def test_repair_disabled_returns_raw_without_calling_llm():
    raw = _raw_two_pages(_PREV, _CURR)
    cfg = type('C', (), {'enable_phase25': False})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair') as m:
        out = repair_format_with_llm(raw)
    assert out == raw
    assert not m.called


def _fake_align_to_4cols(ref_lines, frag_lines):
    """模拟 LLM：保留每个数据行原文，补空列到 4 列，去掉分隔行。"""
    out = []
    for l in frag_lines:
        if phase25._is_table_row(l) and not phase25._is_separator_row(l):
            cells = [c.strip() for c in l.strip().strip('|').split('|')]
            while len(cells) < 4:
                cells.append('')
            out.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(out)


def test_repair_chains_three_page_table():
    # 同一张 4 列表跨 3 页：page3、page4 都应并回 page2，合成一张完整表
    prev = ("| 序号 | 文件名称 | 文件编号 | 备注 |\n| :--- | :--- | :--- | :--- |\n"
            "| 1 | a | X1 | |")
    mid = "| 2 | b | X2 |\n| :--- | :--- | :--- |\n| 3 | c | X3 |"
    last = "| 4 | d | X4 |\n| :--- | :--- | :--- |\n| 5 | e | X5 |"
    raw = (f"<!-- page: 2 -->\n{prev}\n\n<!-- page: 3 -->\n{mid}\n\n<!-- page: 4 -->\n{last}")
    cfg = type('C', (), {'enable_phase25': True})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair', side_effect=_fake_align_to_4cols):
        out = repair_format_with_llm(raw)
    # 全部 5 行都应并入 page2、且为 4 列
    page2 = out.split('<!-- page: 3 -->')[0]
    for n in ('1', '2', '3', '4', '5'):
        assert f'| {n} |' in page2, f'第{n}行未并入 page2'
    assert '| 5 | e | X5 |  |' in page2


def test_repair_no_boundary_leaves_text_unchanged():
    # 两页各是独立的、带标题的表格 → 不应触发合并
    prev = "## 表A\n\n| a | b |\n| :- | :- |\n| 1 | 2 |"
    curr = "## 表B\n\n| c | d |\n| :- | :- |\n| 3 | 4 |"
    raw = _raw_two_pages(prev, curr)
    cfg = type('C', (), {'enable_phase25': True})()
    with patch.object(phase25, 'get_config', return_value=cfg), \
         patch.object(phase25, '_call_repair') as m:
        out = repair_format_with_llm(raw)
    assert out == raw
    assert not m.called

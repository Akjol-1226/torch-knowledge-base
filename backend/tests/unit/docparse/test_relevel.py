"""Tests for relevel_headings_with_llm and its helpers."""
from unittest.mock import patch

import pytest

from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.relevel import _collect_items, _parse_corrections, relevel_headings_with_llm

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_doc(*pages: tuple[int, list[Heading]]) -> DocumentContext:
    doc = DocumentContext(pdf_path='x.pdf', file_title='测试文档', total_pages=len(pages))
    for page_no, headings in pages:
        doc.page_structures[page_no] = PageStructure(page_no=page_no, headings=headings)
    return doc


def _h(text: str, level: int) -> Heading:
    return Heading(text=text, level=level, type='body_heading')


# ── _collect_items ────────────────────────────────────────────────────────────


def test_collect_items_excludes_toc_pages():
    h1 = _h('章节一', 2)
    h2 = _h('目录项', 2)
    doc = DocumentContext(pdf_path='x.pdf', file_title='T', total_pages=2)
    doc.page_structures[1] = PageStructure(page_no=1, headings=[h1])
    doc.page_structures[2] = PageStructure(page_no=2, headings=[h2], is_toc_page=True)
    result = _collect_items(doc)
    assert len(result) == 1
    assert result[0] == (1, h1)


def test_collect_items_includes_appendix_headings():
    h_body = _h('主节', 2)
    h_app = Heading(text='附表1', level=2, type='appendix_heading')
    doc = DocumentContext(pdf_path='x.pdf', file_title='T', total_pages=1)
    doc.page_structures[1] = PageStructure(
        page_no=1, headings=[h_body], appendix_headings=[h_app]
    )
    result = _collect_items(doc)
    assert len(result) == 2


# ── _parse_corrections ────────────────────────────────────────────────────────


def test_parse_corrections_applies_level_change():
    h = _h('附表1', 2)
    collected = [(1, h)]
    raw = '[{"page": 1, "text": "附表1", "level": 3}]'
    corrections = _parse_corrections(raw, collected)
    assert corrections == [3]


def test_parse_corrections_count_mismatch_returns_none():
    h = _h('章节一', 2)
    collected = [(1, h)]
    raw = '[]'
    corrections = _parse_corrections(raw, collected)
    assert corrections == [None]


def test_parse_corrections_text_mismatch_returns_none():
    h = _h('章节一', 2)
    collected = [(1, h)]
    raw = '[{"page": 1, "text": "章节二", "level": 3}]'
    corrections = _parse_corrections(raw, collected)
    assert corrections == [None]


def test_parse_corrections_invalid_level_returns_none():
    h = _h('章节一', 2)
    collected = [(1, h)]
    raw = '[{"page": 1, "text": "章节一", "level": 1}]'  # level=1 禁止
    corrections = _parse_corrections(raw, collected)
    assert corrections == [None]


def test_parse_corrections_level_out_of_range():
    h = _h('章节一', 2)
    collected = [(1, h)]
    raw = '[{"page": 1, "text": "章节一", "level": 7}]'  # >6 不合法
    corrections = _parse_corrections(raw, collected)
    assert corrections == [None]


def test_parse_corrections_no_json_array_raises():
    h = _h('章节一', 2)
    collected = [(1, h)]
    with pytest.raises(ValueError, match='relevel'):
        _parse_corrections('抱歉，无法处理该请求。', collected)


# ── relevel_headings_with_llm ─────────────────────────────────────────────────


def _make_config_with_relevel(enable: bool):
    from app.core.docparse.config import Config
    return Config(
        api_key='k', api_base='b', model='m', outline_model='m',
        relevel_model='m', relevel_max_tokens=32768, relevel_timeout=600.0,
        enable_relevel=enable,
        enable_thinking=False, temperature=0, top_p=0.1,
        max_tokens=8192, seed=None, pdf_render_dpi=600,
        max_previous_tail_chars=300, pymupdf_text_min_chars=50,
        pymupdf_structure_confidence_min=0.45,
        phase2_max_workers=16, repair_tail_continuations=False,
    )


def test_relevel_applies_corrections():
    h_section = _h('G01 配料工序工艺规程', 2)
    h_appendix = _h('附表1 配料参数', 2)  # 错误：应为 level=3
    doc = _make_doc((1, [h_section, h_appendix]))

    fake_response = (
        '[{"page": 1, "text": "G01 配料工序工艺规程", "level": 2},'
        ' {"page": 1, "text": "附表1 配料参数", "level": 3}]'
    )

    with (
        patch('app.core.docparse.relevel.get_config', return_value=_make_config_with_relevel(True)),
        patch('app.core.docparse.relevel.call_llm', return_value=fake_response),
    ):
        relevel_headings_with_llm(doc)

    assert h_section.level == 2   # 不变
    assert h_appendix.level == 3  # 修正


def test_relevel_disabled_when_enable_relevel_false():
    h = _h('附表1', 2)
    doc = _make_doc((1, [h]))

    with (
        patch('app.core.docparse.relevel.get_config', return_value=_make_config_with_relevel(False)),
        patch('app.core.docparse.relevel.call_llm') as mock_call,
    ):
        relevel_headings_with_llm(doc)

    mock_call.assert_not_called()
    assert h.level == 2  # 未改变


def test_relevel_falls_back_on_llm_error():
    h = _h('附表1', 2)
    doc = _make_doc((1, [h]))

    with (
        patch('app.core.docparse.relevel.get_config', return_value=_make_config_with_relevel(True)),
        patch('app.core.docparse.relevel.call_llm', side_effect=RuntimeError('API error')),
    ):
        relevel_headings_with_llm(doc)  # 不应抛出异常

    assert h.level == 2  # 回退，原值不变


def test_relevel_unchanged_level_not_counted():
    h = _h('G01 配料工序工艺规程', 2)
    doc = _make_doc((1, [h]))

    fake_response = '[{"page": 1, "text": "G01 配料工序工艺规程", "level": 2}]'

    with (
        patch('app.core.docparse.relevel.get_config', return_value=_make_config_with_relevel(True)),
        patch('app.core.docparse.relevel.call_llm', return_value=fake_response),
    ):
        relevel_headings_with_llm(doc)

    assert h.level == 2

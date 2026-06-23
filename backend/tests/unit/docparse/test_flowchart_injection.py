"""Tests for flowchart section injection into build_page_context."""
from app.core.docparse.convert_page import build_page_context
from app.core.docparse.models import (
    DocumentContext,
    Heading,
    PageRegion,
    PageStructure,
)
from app.core.docparse.structure_enrich import FLOWCHART_REQUIRED_SECTIONS


def _make_flowchart_doc(page_no: int = 3) -> tuple[DocumentContext, str]:
    """Return a DocumentContext with a flowchart page and matching raw text."""
    doc = DocumentContext(
        pdf_path='test.pdf',
        file_title='CTK41B型多层片式瓷介固定电容器工艺文件',
        total_pages=10,
    )
    ps = PageStructure(
        page_no=page_no,
        headings=[
            Heading(
                text='CTK41B型多层片式瓷介固定电容器工艺生产流程图',
                level=2,
                type='body_heading',
                confidence=0.9,
            )
        ],
        regions=[PageRegion(type='figure', notes='工艺流程图区域')],
    )
    doc.page_structures[page_no] = ps
    raw_text = 'CTK41B型多层片式瓷介固定电容器工艺生产流程图 GJB'
    return doc, raw_text


def _make_non_flowchart_doc(page_no: int = 2) -> tuple[DocumentContext, str]:
    """Return a DocumentContext with a plain body page (no flowchart)."""
    doc = DocumentContext(
        pdf_path='test.pdf',
        file_title='某产品工艺文件',
        total_pages=5,
    )
    ps = PageStructure(
        page_no=page_no,
        headings=[
            Heading(text='1 范围', level=2, type='body_heading', confidence=0.9)
        ],
    )
    doc.page_structures[page_no] = ps
    raw_text = '1 范围 本规程规定了产品加工方法。'
    return doc, raw_text


# ---------------------------------------------------------------------------
# 正面测试：流程图页应注入五个子节
# ---------------------------------------------------------------------------

class TestFlowchartSectionInjection:
    def test_five_sections_present_in_known_headings(self):
        """build_page_context 对流程图页返回的 known_headings 包含全部五个子节。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        texts = [h.text for h in ctx.known_headings_on_this_page]
        for section in FLOWCHART_REQUIRED_SECTIONS:
            assert section in texts, f'缺少流程图子节: {section}'

    def test_sections_appended_after_real_headings(self):
        """五个子节排在真实标题之后。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        headings = ctx.known_headings_on_this_page
        real_indices = [
            i for i, h in enumerate(headings)
            if h.text not in FLOWCHART_REQUIRED_SECTIONS
        ]
        section_indices = [
            i for i, h in enumerate(headings)
            if h.text in FLOWCHART_REQUIRED_SECTIONS
        ]
        if real_indices and section_indices:
            assert max(real_indices) < min(section_indices), (
                '五个子节应排在真实标题之后'
            )

    def test_sections_have_flowchart_section_type(self):
        """注入的子节 type 为 'flowchart_section'。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        for h in ctx.known_headings_on_this_page:
            if h.text in FLOWCHART_REQUIRED_SECTIONS:
                assert h.type == 'flowchart_section', (
                    f'子节 {h.text!r} 的 type 应为 flowchart_section, 实际为 {h.type!r}'
                )

    def test_sections_level_is_section_level_plus_one(self):
        """子节 level = section_level + 1（≥2，≤6）。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        section_headings = [
            h for h in ctx.known_headings_on_this_page
            if h.text in FLOWCHART_REQUIRED_SECTIONS
        ]
        assert section_headings, '应注入至少一个子节'
        for h in section_headings:
            assert 2 <= h.level <= 6, (
                f'子节 level 应在 [2,6] 内，实际为 {h.level}'
            )

    def test_require_flowchart_structure_flag_set(self):
        """流程图页的 require_flowchart_structure 为 True。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        assert ctx.require_flowchart_structure is True

    def test_exactly_five_sections_injected(self):
        """恰好注入五个子节，不多不少。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        injected = [
            h for h in ctx.known_headings_on_this_page
            if h.text in FLOWCHART_REQUIRED_SECTIONS
        ]
        assert len(injected) == len(FLOWCHART_REQUIRED_SECTIONS)

    def test_sections_order_matches_required_sections(self):
        """注入子节的顺序与 FLOWCHART_REQUIRED_SECTIONS 一致。"""
        doc, raw = _make_flowchart_doc()
        ctx = build_page_context(doc, 3, None, raw)
        injected_texts = [
            h.text for h in ctx.known_headings_on_this_page
            if h.text in FLOWCHART_REQUIRED_SECTIONS
        ]
        assert injected_texts == list(FLOWCHART_REQUIRED_SECTIONS)


# ---------------------------------------------------------------------------
# 负面测试：非流程图页不注入子节
# ---------------------------------------------------------------------------

class TestNoInjectionOnNonFlowchartPage:
    def test_no_sections_on_plain_page(self):
        """普通页不注入流程图子节。"""
        doc, raw = _make_non_flowchart_doc()
        ctx = build_page_context(doc, 2, None, raw)
        texts = [h.text for h in ctx.known_headings_on_this_page]
        for section in FLOWCHART_REQUIRED_SECTIONS:
            assert section not in texts, f'普通页不应包含子节: {section}'

    def test_toc_page_no_injection(self):
        """目录页不注入流程图子节（即使含流程图关键词）。"""
        doc = DocumentContext(
            pdf_path='test.pdf',
            file_title='工艺文件目录',
            total_pages=8,
        )
        ps = PageStructure(
            page_no=1,
            is_toc_page=True,
            headings=[Heading(text='工艺文件目录', level=2, type='body_heading')],
            regions=[PageRegion(type='toc', notes='目录')],
        )
        doc.page_structures[1] = ps
        raw = '| 1 | CTK41B型多层片式瓷介固定电容器工艺生产流程图 | HJ4.603 |'
        ctx = build_page_context(doc, 1, None, raw)
        texts = [h.text for h in ctx.known_headings_on_this_page]
        for section in FLOWCHART_REQUIRED_SECTIONS:
            assert section not in texts

    def test_process_regulation_page_no_injection(self):
        """工艺规程页（含 G01/工艺规程标志）不注入流程图子节。"""
        doc = DocumentContext(
            pdf_path='test.pdf',
            file_title='G01工艺文件',
            total_pages=5,
        )
        ps = PageStructure(
            page_no=2,
            headings=[
                Heading(
                    text='G01 配料工序工艺规程（关键工序）',
                    level=3,
                    type='body_heading',
                )
            ],
        )
        doc.page_structures[2] = ps
        raw = '火炬电子 G01 配料工序工艺规程（关键工序） 产品名称'
        ctx = build_page_context(doc, 2, None, raw)
        texts = [h.text for h in ctx.known_headings_on_this_page]
        for section in FLOWCHART_REQUIRED_SECTIONS:
            assert section not in texts


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------

class TestFlowchartInjectionEdgeCases:
    def test_level_capped_at_six(self):
        """当 section_level 已为 5，子节 level 不超过 6。"""
        doc = DocumentContext(
            pdf_path='test.pdf',
            file_title='工艺文件',
            total_pages=10,
        )
        ps = PageStructure(
            page_no=5,
            headings=[
                Heading(
                    text='CTK41B型多层片式瓷介固定电容器工艺生产流程图',
                    level=5,
                    type='body_heading',
                )
            ],
            regions=[PageRegion(type='figure', notes='流程图')],
        )
        doc.page_structures[5] = ps
        raw = 'CTK41B型多层片式瓷介固定电容器工艺生产流程图 GJB'
        ctx = build_page_context(doc, 5, None, raw)
        injected = [
            h for h in ctx.known_headings_on_this_page
            if h.text in FLOWCHART_REQUIRED_SECTIONS
        ]
        for h in injected:
            assert h.level <= 6, f'level 不应超过 6，实际为 {h.level}'

    def test_no_duplicate_injection_when_already_in_headings(self):
        """若 page_structures 中已有同名子节标题，不重复注入。"""
        doc = DocumentContext(
            pdf_path='test.pdf',
            file_title='工艺文件',
            total_pages=10,
        )
        ps = PageStructure(
            page_no=6,
            headings=[
                Heading(
                    text='CTK41B型多层片式瓷介固定电容器工艺生产流程图',
                    level=2,
                    type='body_heading',
                ),
                Heading(
                    text='节点列表',
                    level=3,
                    type='flowchart_section',
                ),
            ],
            regions=[PageRegion(type='figure', notes='流程图')],
        )
        doc.page_structures[6] = ps
        raw = 'CTK41B型多层片式瓷介固定电容器工艺生产流程图 GJB'
        ctx = build_page_context(doc, 6, None, raw)
        count = sum(
            1 for h in ctx.known_headings_on_this_page if h.text == '节点列表'
        )
        assert count == 1, f'节点列表 应恰好出现 1 次，实际出现 {count} 次'

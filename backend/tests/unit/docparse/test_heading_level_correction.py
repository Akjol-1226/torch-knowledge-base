"""Tests for correct_heading_levels_from_outline postprocess pass.

Tests verify that Phase 2 output headings whose level differs from Phase 1.5
are corrected to match Phase 1.5, with appropriate exclusions.
"""
from __future__ import annotations

from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.postprocess import correct_heading_levels_from_outline


def _make_doc_ctx(pages: dict[int, list[Heading]]) -> DocumentContext:
    """Build a minimal DocumentContext with given page headings."""
    page_structures: dict[int, PageStructure] = {}
    for pno, headings in pages.items():
        page_structures[pno] = PageStructure(page_no=pno, headings=headings)
    return DocumentContext(
        pdf_path='fake.pdf',
        file_title='测试文档',
        total_pages=max(pages.keys()) if pages else 1,
        page_structures=page_structures,
    )


# ── 精确匹配测试 ──────────────────────────────────────────────────────────────

class TestExactMatch:
    def test_corrects_shallow_heading_to_deeper_level(self):
        """Phase 2 outputs H3 but Phase 1.5 says H6 → correct to H6."""
        raw = '<!-- page: 85 -->\n### 3.1.2 可靠性定性和定量要求\n正文内容\n'
        ctx = _make_doc_ctx({
            85: [Heading(text='3.1.2 可靠性定性和定量要求', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '###### 3.1.2 可靠性定性和定量要求' in result

    def test_corrects_another_shallow_heading(self):
        """Phase 2 outputs H3 but Phase 1.5 says H6 for env stress test."""
        raw = '<!-- page: 91 -->\n### 3.2.2.4 环境应力筛选及环境试验\n正文\n'
        ctx = _make_doc_ctx({
            91: [Heading(text='3.2.2.4 环境应力筛选及环境试验', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '###### 3.2.2.4 环境应力筛选及环境试验' in result

    def test_corrects_h3_to_h4(self):
        """Phase 2 outputs H3 but Phase 1.5 says H4."""
        raw = '<!-- page: 58 -->\n### 1.1 功能分析：\n内容\n'
        ctx = _make_doc_ctx({
            58: [Heading(text='1.1 功能分析：', level=4)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '#### 1.1 功能分析：' in result

    def test_correct_level_unchanged(self):
        """If Phase 2 level matches Phase 1.5, line is not modified."""
        raw = '<!-- page: 10 -->\n### 1.1 功能分析\n内容\n'
        ctx = _make_doc_ctx({
            10: [Heading(text='1.1 功能分析', level=3)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 1.1 功能分析' in result
        assert '#### 1.1 功能分析' not in result

    def test_multiple_headings_on_same_page(self):
        """Multiple headings on the same page are all corrected."""
        raw = (
            '<!-- page: 20 -->\n'
            '### 1.1 功能分析\n'
            '### 1.2 性能要求\n'
            '正文\n'
        )
        ctx = _make_doc_ctx({
            20: [
                Heading(text='1.1 功能分析', level=4),
                Heading(text='1.2 性能要求', level=5),
            ]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '#### 1.1 功能分析' in result
        assert '##### 1.2 性能要求' in result

    def test_case_insensitive_normalization(self):
        """Normalization handles full-width characters."""
        raw = '<!-- page: 30 -->\n### 1.1 功能分析：\n内容\n'
        ctx = _make_doc_ctx({
            30: [Heading(text='1.1 功能分析：', level=5)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '##### 1.1 功能分析：' in result


# ── 模糊匹配测试 ──────────────────────────────────────────────────────────────

class TestFuzzyMatch:
    def test_containment_match_p2_text_contains_p1_text(self):
        """Phase 2 heading contains Phase 1.5 heading text → match."""
        raw = '<!-- page: 40 -->\n### 3.1.2 可靠性定性和定量要求（详细）\n内容\n'
        ctx = _make_doc_ctx({
            40: [Heading(text='3.1.2 可靠性定性和定量要求', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '###### 3.1.2 可靠性定性和定量要求（详细）' in result

    def test_containment_match_p1_text_contains_p2_text(self):
        """Phase 1.5 heading text contains Phase 2 heading text → match."""
        raw = '<!-- page: 41 -->\n### 可靠性要求\n内容\n'
        ctx = _make_doc_ctx({
            41: [Heading(text='3.1.2 可靠性要求（详细版）', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '###### 可靠性要求' in result

    def test_short_text_not_fuzzy_matched(self):
        """Short heading texts are not FUZZY (substring) matched — avoids false positives."""
        raw = '<!-- page: 50 -->\n### 概述\n内容\n'
        ctx = _make_doc_ctx({
            50: [Heading(text='概述要点说明', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        # '概述' 是 '概述要点说明' 的子串，但短文本不做模糊匹配 → 不纠正
        assert '### 概述' in result
        assert '###### 概述' not in result

    def test_short_text_exact_match_corrected(self):
        """Short heading texts that EXACTLY equal a P1 heading are corrected to its level."""
        raw = '<!-- page: 50 -->\n### 目的\n内容\n'
        ctx = _make_doc_ctx({
            50: [Heading(text='目的', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '###### 目的' in result

    def test_no_match_different_content(self):
        """Completely different heading text → no correction."""
        raw = '<!-- page: 60 -->\n### 完全不同的标题\n内容\n'
        ctx = _make_doc_ctx({
            60: [Heading(text='毫无关联的另一个标题', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 完全不同的标题' in result


# ── 排除名单测试 ──────────────────────────────────────────────────────────────

class TestExclusionList:
    def test_h1_not_modified(self):
        """H1 headings are never corrected."""
        raw = '<!-- page: 1 -->\n# 文档总标题\n内容\n'
        ctx = _make_doc_ctx({
            1: [Heading(text='文档总标题', level=3)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '# 文档总标题' in result
        assert '## 文档总标题' not in result
        assert '### 文档总标题' not in result

    def test_flowchart_section_not_modified(self):
        """Flowchart meta-sections (节点列表, 关系列表, etc.) are not corrected."""
        raw = (
            '<!-- page: 70 -->\n'
            '### 流程图/架构图信息\n'
            '### 节点列表\n'
            '### 关系列表\n'
            '### 流程链路总结\n'
            '### Mermaid 图\n'
        )
        ctx = _make_doc_ctx({
            70: [
                Heading(text='流程图/架构图信息', level=6),
                Heading(text='节点列表', level=6),
                Heading(text='关系列表', level=6),
                Heading(text='流程链路总结', level=6),
                Heading(text='Mermaid 图', level=6),
            ]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        # None of these should be promoted to H6
        assert '### 流程图/架构图信息' in result
        assert '###### 流程图/架构图信息' not in result
        assert '### 节点列表' in result
        assert '### 关系列表' in result
        assert '### 流程链路总结' in result
        assert '### Mermaid 图' in result

    def test_stack_excluded_headings_not_modified(self):
        """STACK_EXCLUDED_HEADING_RE patterns are not corrected."""
        raw = '<!-- page: 75 -->\n### 工艺流程图\n内容\n'
        ctx = _make_doc_ctx({
            75: [Heading(text='工艺流程图', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 工艺流程图' in result
        assert '###### 工艺流程图' not in result


# ── 多页处理测试 ──────────────────────────────────────────────────────────────

class TestMultiPageHandling:
    def test_each_page_uses_own_headings(self):
        """Different pages use their own Phase 1.5 headings for correction."""
        raw = (
            '<!-- page: 10 -->\n### 功能分析要求\n内容A\n'
            '<!-- page: 20 -->\n### 性能指标评估\n内容B\n'
        )
        ctx = _make_doc_ctx({
            10: [Heading(text='功能分析要求', level=5)],
            20: [Heading(text='性能指标评估', level=4)],
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '##### 功能分析要求' in result
        assert '#### 性能指标评估' in result

    def test_page_not_in_context_leaves_headings_unchanged(self):
        """If a page has no Phase 1.5 data, headings are unchanged."""
        raw = '<!-- page: 999 -->\n### 孤立页标题内容\n内容\n'
        ctx = _make_doc_ctx({1: [Heading(text='其他页标题', level=6)]})
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 孤立页标题内容' in result

    def test_no_document_context_no_crash(self):
        """Empty document context → no crash, no changes."""
        raw = '<!-- page: 1 -->\n### 功能分析标题\n内容\n'
        ctx = _make_doc_ctx({})
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 功能分析标题' in result


# ── 代码块内不修改测试 ────────────────────────────────────────────────────────

class TestCodeBlockSkipped:
    def test_heading_inside_code_block_not_corrected(self):
        """Headings inside fenced code blocks are not corrected."""
        raw = (
            '<!-- page: 80 -->\n'
            '```\n'
            '### 代码块内标题\n'
            '```\n'
        )
        ctx = _make_doc_ctx({
            80: [Heading(text='代码块内标题', level=6)]
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '### 代码块内标题' in result
        assert '###### 代码块内标题' not in result


# ── appendix_headings 也参与匹配 ─────────────────────────────────────────────

class TestAppendixHeadings:
    def test_appendix_heading_can_be_corrected(self):
        """Headings in appendix_headings list are also used for correction."""
        raw = '<!-- page: 90 -->\n### 附件1：测试规范\n内容\n'
        ps = PageStructure(
            page_no=90,
            appendix_headings=[Heading(text='附件1：测试规范', level=5)],
        )
        ctx = DocumentContext(
            pdf_path='fake.pdf',
            file_title='测试文档',
            total_pages=100,
            page_structures={90: ps},
        )
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '##### 附件1：测试规范' in result


# ── 每页级别（以 Phase 1 为主，不做全局拍平）─────────────────────────────────

class TestPerPageLevel:
    def test_uses_per_page_level_not_flattened(self):
        """同一标题文本在不同页、Phase 1 级别不同时，各页按各自 Phase 1 级别校正，不拍平。"""
        raw = (
            '<!-- page: 1 -->\n### G01 配料工艺规程\n内容\n'
            '<!-- page: 5 -->\n### G01 配料工艺规程\n内容\n'
        )
        ctx = _make_doc_ctx({
            1: [Heading(text='G01 配料工艺规程', level=2)],
            5: [Heading(text='G01 配料工艺规程', level=3)],
        })
        result = correct_heading_levels_from_outline(raw, ctx)
        assert '## G01 配料工艺规程' in result   # page1 → 本页 Phase 1 = H2
        assert '### G01 配料工艺规程' in result  # page5 → 本页 Phase 1 = H3

    def test_postprocess_markdown_uses_per_page_level(self):
        """端到端：同一标题在不同页按各自 Phase 1 级别输出（不再强制全局一致）。"""
        from app.core.docparse.postprocess import postprocess_markdown

        doc = DocumentContext(
            pdf_path='fake.pdf',
            file_title='测试',
            total_pages=5,
            page_structures={
                1: PageStructure(page_no=1, headings=[Heading(text='G01 配料工艺规程', level=2)]),
                5: PageStructure(page_no=5, headings=[Heading(text='G01 配料工艺规程', level=3)]),
            },
        )
        raw = (
            '<!-- page: 1 -->\n### G01 配料工艺规程\n内容\n'
            '<!-- page: 5 -->\n### G01 配料工艺规程\n内容\n'
        )
        result = postprocess_markdown(raw, '测试', doc, debug=True)
        p1_block = result.split('<!-- page: 5 -->')[0]
        p5_block = result.split('<!-- page: 5 -->')[1]
        assert '## G01 配料工艺规程' in p1_block
        assert '### G01 配料工艺规程' in p5_block

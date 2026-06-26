"""Tests for filter_unmatched_p2_headings postprocessing pass."""
from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.postprocess import filter_unmatched_p2_headings


def _ctx(*page_structures: PageStructure) -> DocumentContext:
    ctx = DocumentContext(pdf_path="test.pdf", file_title="Test", total_pages=200)
    ctx.page_structures = {ps.page_no: ps for ps in page_structures}
    return ctx


def _ps(pno: int, headings=None, is_toc: bool = False) -> PageStructure:
    ps = PageStructure(page_no=pno, is_toc_page=is_toc)
    ps.headings = list(headings or [])
    return ps


def _h(text: str, level: int = 2, htype: str = "body_heading") -> Heading:
    return Heading(text=text, level=level, type=htype)


# ── 基本匹配逻辑 ──────────────────────────────────────────────────────────────

class TestBasicMatching:
    def test_matched_heading_kept(self):
        text = "<!-- page: 1 -->\n## G01 配料工序\n\nsome content\n"
        ctx = _ctx(_ps(1, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## G01 配料工序" in result

    def test_unmatched_heading_demoted_to_bold(self):
        text = "<!-- page: 1 -->\n## 随意标题\n\nsome content\n"
        ctx = _ctx(_ps(1, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## 随意标题" not in result
        assert "**随意标题**" in result

    def test_fuzzy_match_kept(self):
        """Heading text is a substring of P1.5 heading → kept."""
        text = "<!-- page: 3 -->\n## G02 瓷浆精炼工序\n\ncontent\n"
        ctx = _ctx(_ps(3, [_h("火炬电子 G02 瓷浆精炼工序工艺规程", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## G02 瓷浆精炼工序" in result

    def test_correct_level_after_correction_kept(self):
        """After correct_heading_levels, heading at P1.5 level is kept."""
        text = "<!-- page: 4 -->\n### 检验项目\n\ncontent\n"
        ctx = _ctx(_ps(4, [_h("检验项目", 3)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "### 检验项目" in result


# ── H1 和特殊标题保护 ─────────────────────────────────────────────────────────

class TestProtectedHeadings:
    def test_h1_always_kept(self):
        """H1 headings are the document title added by the system — never demote."""
        text = "<!-- page: 1 -->\n# 文档总标题\n\ncontent\n"
        ctx = _ctx(_ps(1, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "# 文档总标题" in result

    def test_flowchart_page_all_headings_kept(self):
        """Page with flowchart_section type heading: keep all P2 headings."""
        text = (
            "<!-- page: 5 -->\n"
            "## 生产工艺流程图\n\n"
            "### 流程图/架构图信息\n\n"
            "### 节点列表\n\n"
            "### 关系列表\n\n"
            "### 流程链路总结\n\n"
            "### Mermaid 图\n"
        )
        ctx = _ctx(_ps(5, [_h("生产工艺流程图", 2, htype="flowchart_section")]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "### 流程图/架构图信息" in result
        assert "### 节点列表" in result
        assert "### 关系列表" in result
        assert "### 流程链路总结" in result
        assert "### Mermaid 图" in result

    def test_toc_page_headings_kept(self):
        text = "<!-- page: 2 -->\n## 目录\n\ncontent\n"
        ctx = _ctx(_ps(2, [_h("目录", 2)], is_toc=True))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## 目录" in result

    def test_page_without_p15_structure_unchanged(self):
        text = "<!-- page: 99 -->\n## 某个标题\n\ncontent\n"
        ctx = _ctx(_ps(1, [_h("其他页的标题", 2)]))  # page 99 has no structure
        result = filter_unmatched_p2_headings(text, ctx)
        assert result == text

    def test_code_block_heading_not_demoted(self):
        text = (
            "<!-- page: 6 -->\n"
            "```\n"
            "## 代码里的标题\n"
            "```\n"
        )
        ctx = _ctx(_ps(6, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## 代码里的标题" in result


# ── 边界情况 ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_page_structures_unchanged(self):
        text = "<!-- page: 1 -->\n## 某标题\n\ncontent\n"
        ctx = DocumentContext(pdf_path="test.pdf", file_title="Test", total_pages=1)
        result = filter_unmatched_p2_headings(text, ctx)
        assert result == text

    def test_multiple_headings_mixed(self):
        """Matched headings kept, unmatched demoted."""
        text = (
            "<!-- page: 7 -->\n"
            "## G03 涂布工序\n\n"
            "### 本工序要点\n\n"
            "#### 噪声标题\n\n"
            "content\n"
        )
        ctx = _ctx(_ps(7, [
            _h("G03 涂布工序", 2),
            _h("本工序要点", 3),
        ]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## G03 涂布工序" in result
        assert "### 本工序要点" in result
        assert "#### 噪声标题" not in result
        assert "**噪声标题**" in result

    def test_demoted_heading_text_preserved(self):
        """The heading text itself is preserved in the bold replacement."""
        text = "<!-- page: 8 -->\n### 完整标题文本内容\n\ncontent\n"
        ctx = _ctx(_ps(8, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "**完整标题文本内容**" in result

    def test_no_page_markers_unchanged(self):
        """Text without page markers is returned unchanged."""
        text = "## 某标题\n\ncontent\n"
        ctx = _ctx(_ps(1, [_h("G01 配料工序", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert result == text


# ── 文本归一化匹配（空格 / 短标题）─────────────────────────────────────────────

class TestNormalizedMatching:
    def test_internal_space_difference_kept(self):
        """VLM 在字母与汉字间插入空格（CTK41B 型）不应导致与 P1（CTK41B型）匹配失败。"""
        text = "<!-- page: 1 -->\n## CTK41B 型多层片式瓷介固定电容器关键工序及特殊工序控制计划\n\ncontent\n"
        ctx = _ctx(_ps(1, [_h("CTK41B型多层片式瓷介固定电容器关键工序及特殊工序控制计划", 2)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "## CTK41B 型多层片式瓷介固定电容器关键工序及特殊工序控制计划" in result
        assert "**CTK41B" not in result

    def test_short_heading_exact_match_kept(self):
        """短标题（'目的' 2 字，低于模糊阈值）只要与 P1 精确相等就应保留。"""
        text = "<!-- page: 2 -->\n### 目的\n\ncontent\n"
        ctx = _ctx(_ps(2, [_h("目的", 3)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "### 目的" in result
        assert "**目的**" not in result

    def test_short_heading_no_false_positive(self):
        """短标题与 P1 不相等时仍应降级（精确匹配不得放宽为子串）。"""
        text = "<!-- page: 3 -->\n### 范围\n\ncontent\n"
        ctx = _ctx(_ps(3, [_h("目的", 3)]))
        result = filter_unmatched_p2_headings(text, ctx)
        assert "### 范围" not in result
        assert "**范围**" in result

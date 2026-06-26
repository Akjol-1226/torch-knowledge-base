"""Tests for repromote_demoted_phase1_headings — re-promote headings that
heuristic demotion passes turned into plain text / list items, when Phase 1
recorded them as headings (Phase 1 is authoritative)."""
from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.postprocess import repromote_demoted_phase1_headings


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


class TestRepromote:
    def test_numbered_plain_line_repromoted(self):
        """'1. 大纲编制依据' demoted to plain text → re-promote to Phase 1 heading."""
        text = "<!-- page: 1 -->\n1. 大纲编制依据\n正文内容\n"
        ctx = _ctx(_ps(1, [_h("1. 大纲编制依据", 3)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "### 1. 大纲编制依据" in result

    def test_list_item_repromoted(self):
        """'- （定型）设计文件输出清单' demoted to list item → re-promote."""
        text = "<!-- page: 2 -->\n- （定型）设计文件输出清单\n正文\n"
        ctx = _ctx(_ps(2, [_h("（定型）设计文件输出清单", 2)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "## （定型）设计文件输出清单" in result
        assert "- （定型）设计文件输出清单" not in result

    def test_uses_phase1_level(self):
        text = "<!-- page: 3 -->\n1. 某节\n"
        ctx = _ctx(_ps(3, [_h("1. 某节", 4)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "#### 1. 某节" in result

    def test_bold_phase1_table_title_promoted(self):
        """Phase 1 标为标题、但 Phase 2 输出成加粗的表题 → 按 Phase 1 级别提升为标题。"""
        text = "<!-- page: 4 -->\n**表 1 技术指标**\n| a | b |\n"
        ctx = _ctx(_ps(4, [_h("表 1 技术指标", 5)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "##### 表 1 技术指标" in result
        assert "**表 1 技术指标**" not in result

    def test_bold_table_title_with_redaction_promoted(self):
        """Phase 1 文本含脱敏占位(XXX)、最终已去占位 → 仍匹配并提升，且用去占位后的文本。"""
        text = "<!-- page: 4 -->\n**表 3 CTK41B(瓷粉)技术指标**\n| a |\n"
        ctx = _ctx(_ps(4, [_h("表 3 CTK41B(XXX瓷粉)技术指标", 6)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "###### 表 3 CTK41B(瓷粉)技术指标" in result
        assert "XXX" not in result  # 不得还原脱敏占位

    def test_repromoted_list_item_strips_bullet(self):
        """列表项提升后去掉「- 」项目符号。"""
        text = "<!-- page: 5 -->\n- （定型）输出清单\n"
        ctx = _ctx(_ps(5, [_h("（定型）输出清单", 2)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "## （定型）输出清单" in result
        assert "- （定型）输出清单" not in result

    def test_bold_not_in_phase1_stays_bold(self):
        """普通加粗（非 Phase 1 标题）保持加粗，不被提升。"""
        text = "<!-- page: 4 -->\n**重点提示**\n正文\n"
        ctx = _ctx(_ps(4, [_h("某真实章节", 2)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "**重点提示**" in result
        assert "## 重点提示" not in result

    def test_unrelated_list_item_not_promoted(self):
        """A real list item with no Phase 1 match stays a list item."""
        text = "<!-- page: 5 -->\n- 普通列表项内容\n"
        ctx = _ctx(_ps(5, [_h("某个真实标题", 2)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "- 普通列表项内容" in result
        assert "##" not in result

    def test_already_heading_no_duplicate(self):
        """If the heading already exists on the page, the matching body line is not also promoted."""
        text = "<!-- page: 6 -->\n## 重要章节\n参见 重要章节 的说明\n"
        ctx = _ctx(_ps(6, [_h("重要章节", 2)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        # the sentence is not a whole-line match, must stay untouched; only one heading
        assert result.count("## 重要章节") == 1
        assert "参见 重要章节 的说明" in result

    def test_table_cell_not_touched(self):
        text = "<!-- page: 7 -->\n<td colspan=\"4\"><b>主要评审内容</b></td>\n"
        ctx = _ctx(_ps(7, [_h("主要评审内容", 3)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "<td colspan=\"4\"><b>主要评审内容</b></td>" in result
        assert "### 主要评审内容" not in result

    def test_code_block_not_touched(self):
        text = "<!-- page: 8 -->\n```\n1. 大纲编制依据\n```\n"
        ctx = _ctx(_ps(8, [_h("1. 大纲编制依据", 3)]))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "### 1. 大纲编制依据" not in result

    def test_genuine_toc_entry_not_promoted(self):
        """真目录页：目录项不在 ps.headings 中（符合 Phase 1 目录页规则）→ 不提升。"""
        text = "<!-- page: 9 -->\n- 第一章 概述\n- 第二章 设计\n"
        ctx = _ctx(_ps(9, [], is_toc=True))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "- 第一章 概述" in result
        assert "## 第一章 概述" not in result

    def test_toc_misclassified_with_phase1_heading_promoted(self):
        """页被 Phase 1 误判为目录页，但又记录了 body_heading → 以 Phase 1 为主，提升。"""
        text = "<!-- page: 10 -->\n- （定型）设计文件输出清单\n| 1 | 文件 | 编号 |\n"
        ctx = _ctx(_ps(10, [_h("（定型）设计文件输出清单", 2)], is_toc=True))
        result = repromote_demoted_phase1_headings(text, ctx)
        assert "## （定型）设计文件输出清单" in result

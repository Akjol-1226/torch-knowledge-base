"""Tests for inject_missing_headings_from_outline postprocessing pass."""
from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.postprocess import inject_missing_headings_from_outline


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


# ── HTML <th> injection ───────────────────────────────────────────────────────

class TestInjectFromThCell:
    def test_injects_heading_before_table(self):
        text = (
            "<!-- page: 8 -->\n"
            "<table>\n"
            "  <thead><tr>\n"
            "    <th colspan='2'>火炬电子</th>\n"
            "    <th colspan='3'>G01 配料工序工艺规程（关键工序）</th>\n"
            "  </tr></thead>\n"
            "  <tbody><tr><td>content</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(8, [_h("G01 配料工序工艺规程（关键工序）", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G01 配料工序工艺规程（关键工序）" in result
        h_pos = result.index("## G01 配料工序工艺规程（关键工序）")
        t_pos = result.index("<table>")
        assert h_pos < t_pos

    def test_injected_level_matches_p15(self):
        text = (
            "<!-- page: 10 -->\n"
            "<table>\n"
            "  <thead><tr><th colspan='3'>本工序要点：</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(10, [_h("本工序要点：", 3)]))
        result = inject_missing_headings_from_outline(text, ctx)
        lines = result.split('\n')
        assert any(l == "### 本工序要点：" for l in lines)
        assert not any(l == "## 本工序要点：" for l in lines)

    def test_no_duplicate_when_already_markdown(self):
        text = (
            "<!-- page: 8 -->\n"
            "## G01 配料工序工艺规程（关键工序）\n\n"
            "<table>\n"
            "  <thead><tr><th colspan='3'>G01 配料工序工艺规程（关键工序）</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(8, [_h("G01 配料工序工艺规程（关键工序）", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert result.count("## G01 配料工序工艺规程（关键工序）") == 1

    def test_fuzzy_match_th_contains_heading(self):
        """Heading text is a substring of the <th> content."""
        text = (
            "<!-- page: 9 -->\n"
            "<table>\n"
            "  <thead><tr>\n"
            "    <th>火炬电子 G02 瓷浆精炼工序工艺规程 Rev.A</th>\n"
            "  </tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(9, [_h("G02 瓷浆精炼工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G02 瓷浆精炼工序工艺规程" in result

    def test_short_th_text_not_matched(self):
        """<th> content shorter than min chars is ignored."""
        text = (
            "<!-- page: 1 -->\n"
            "<table>\n"
            "  <thead><tr><th>序</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(1, [_h("序", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## 序" not in result


# ── Plain-text line injection ─────────────────────────────────────────────────

class TestInjectFromPlainTextLine:
    def test_plain_text_line_upgraded_to_heading(self):
        text = (
            "<!-- page: 28 -->\n"
            "火炬电子 G02 瓷浆精炼工序工艺规程\n\n"
            "| | | 产品名称 | CTK41B |\n"
            "| :--- | :--- | :--- | :--- |\n"
        )
        ctx = _ctx(_ps(28, [_h("G02 瓷浆精炼工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G02 瓷浆精炼工序工艺规程" in result

    def test_sentence_line_not_upgraded(self):
        """Line with sentence-ending punctuation is not treated as a heading."""
        text = (
            "<!-- page: 5 -->\n"
            "本文件描述了G03 涂布工序工艺规程的详细要求，包含多个步骤。\n\n"
            "<table><thead><tr><th>项目</th></tr></thead>"
            "<tbody><tr><td>x</td></tr></tbody></table>\n"
        )
        ctx = _ctx(_ps(5, [_h("G03 涂布工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G03 涂布工序工艺规程" not in result

    def test_code_block_content_not_upgraded(self):
        """Text inside a fenced code block is never treated as a heading."""
        text = (
            "<!-- page: 6 -->\n"
            "```\n"
            "G04 热处理工序工艺规程\n"
            "```\n"
        )
        ctx = _ctx(_ps(6, [_h("G04 热处理工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G04 热处理工序工艺规程" not in result

    def test_plain_text_inside_html_table_not_upgraded(self):
        """Content inside a <table> block is not treated as a heading."""
        text = (
            "<!-- page: 7 -->\n"
            "<table>\n"
            "  <tbody>\n"
            "    <tr><td>G05 烧结工序工艺规程</td></tr>\n"
            "  </tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(7, [_h("G05 烧结工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G05 烧结工序工艺规程" not in result


# ── Guard rails ───────────────────────────────────────────────────────────────

class TestGuardRails:
    def test_no_inject_for_toc_page(self):
        text = (
            "<!-- page: 2 -->\n"
            "<table>\n"
            "  <thead><tr><th>工艺文件目录</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(2, [_h("工艺文件目录", 2)], is_toc=True))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## 工艺文件目录" not in result

    def test_no_inject_for_flowchart_section_type(self):
        text = (
            "<!-- page: 3 -->\n"
            "<table>\n"
            "  <thead><tr><th>节点列表</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(3, [_h("节点列表", 3, htype="flowchart_section")]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "### 节点列表" not in result

    def test_no_inject_when_heading_not_in_content(self):
        """If heading text is absent from page content entirely, do not inject."""
        text = (
            "<!-- page: 5 -->\n"
            "Some unrelated content.\n\n"
            "<table>\n"
            "  <thead><tr><th>Column A</th><th>Column B</th></tr></thead>\n"
            "  <tbody><tr><td>data</td><td>data</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(5, [_h("G99 完全不存在的章节", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G99 完全不存在的章节" not in result

    def test_no_inject_for_short_heading_text(self):
        text = (
            "<!-- page: 1 -->\n"
            "<table>\n"
            "  <thead><tr><th>AB</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(1, [_h("AB", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## AB" not in result

    def test_empty_page_structures_returns_text_unchanged(self):
        text = "<!-- page: 1 -->\nsome content\n"
        ctx = DocumentContext(pdf_path="test.pdf", file_title="Test", total_pages=1)
        result = inject_missing_headings_from_outline(text, ctx)
        assert result == text

    def test_page_without_structure_unchanged(self):
        text = (
            "<!-- page: 99 -->\n"
            "<table><thead><tr><th>Title</th></tr></thead>"
            "<tbody><tr><td>x</td></tr></tbody></table>\n"
        )
        ctx = _ctx(_ps(1, [_h("Other heading", 2)]))  # page 99 has no structure
        result = inject_missing_headings_from_outline(text, ctx)
        assert result == text


# ── Multi-heading edge cases ──────────────────────────────────────────────────

class TestMultipleHeadings:
    def test_multiple_headings_both_injected(self):
        text = (
            "<!-- page: 15 -->\n"
            "<table>\n"
            "  <thead><tr>\n"
            "    <th>火炬电子</th>\n"
            "    <th colspan='3'>G01 配料工序工艺规程（关键工序）</th>\n"
            "  </tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n\n"
            "附表 1 设备清单\n\n"
            "More content\n"
        )
        ctx = _ctx(_ps(15, [
            _h("G01 配料工序工艺规程（关键工序）", 2),
            _h("附表 1 设备清单", 3),
        ]))
        result = inject_missing_headings_from_outline(text, ctx)
        assert "## G01 配料工序工艺规程（关键工序）" in result
        assert "### 附表 1 设备清单" in result

    def test_heading_found_in_th_not_injected_again_from_plain_text(self):
        """A heading found in <th> is removed from the missing list; plain-text pass skips it."""
        text = (
            "<!-- page: 20 -->\n"
            "G06 印刷工序工艺规程\n\n"  # also appears as plain text
            "<table>\n"
            "  <thead><tr><th>G06 印刷工序工艺规程</th></tr></thead>\n"
            "  <tbody><tr><td>x</td></tr></tbody>\n"
            "</table>\n"
        )
        ctx = _ctx(_ps(20, [_h("G06 印刷工序工艺规程", 2)]))
        result = inject_missing_headings_from_outline(text, ctx)
        # Heading should appear exactly once
        assert result.count("## G06 印刷工序工艺规程") == 1

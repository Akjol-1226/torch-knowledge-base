"""Tests for inject_running_section_headers — fills a multi-page 工序 page-header
on continuation pages where Phase 2 (VLM) skipped transcribing the running header."""
from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.postprocess import inject_running_section_headers


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


class TestInjectRunningHeader:
    def test_injects_on_continuation_page_when_missing(self):
        """P1 has the same header on prev page and this page, but markdown lacks it → inject."""
        text = (
            "<!-- page: 91 -->\n## G12 端附工序工艺规程\n正文a\n"
            "<!-- page: 92 -->\n正文b 续页内容\n"
        )
        ctx = _ctx(
            _ps(91, [_h("G12 端附工序工艺规程", 2)]),
            _ps(92, [_h("G12 端附工序工艺规程", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        # page 92 block should now start with the header
        p92 = result.split("<!-- page: 92 -->")[1]
        assert "## G12 端附工序工艺规程" in p92

    def test_injected_header_precedes_page_body(self):
        text = (
            "<!-- page: 91 -->\n## G12 端附工序工艺规程\n正文a\n"
            "<!-- page: 92 -->\n正文b 续页内容\n"
        )
        ctx = _ctx(
            _ps(91, [_h("G12 端附工序工艺规程", 2)]),
            _ps(92, [_h("G12 端附工序工艺规程", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        p92 = result.split("<!-- page: 92 -->")[1]
        assert p92.index("## G12 端附工序工艺规程") < p92.index("正文b")

    def test_no_inject_if_already_present(self):
        """No duplicate when the page already has the header (allowing VLM spacing)."""
        text = (
            "<!-- page: 91 -->\n## G12 端附工序工艺规程\n正文a\n"
            "<!-- page: 92 -->\n## G12 端附工序工艺规程\n正文b\n"
        )
        ctx = _ctx(
            _ps(91, [_h("G12 端附工序工艺规程", 2)]),
            _ps(92, [_h("G12 端附工序工艺规程", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        assert result.count("## G12 端附工序工艺规程") == 2

    def test_space_variant_counts_as_present(self):
        """VLM spacing ('G12 端附…' vs P1 'G12端附…') must not trigger a duplicate inject."""
        text = (
            "<!-- page: 91 -->\n## G12 端附工序工艺规程\n正文a\n"
            "<!-- page: 92 -->\n## G12 端附 工序工艺规程\n正文b\n"
        )
        ctx = _ctx(
            _ps(91, [_h("G12端附工序工艺规程", 2)]),
            _ps(92, [_h("G12端附工序工艺规程", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        # only the existing (spaced) one — no extra injected copy
        assert "## G12 端附 工序工艺规程" in result
        assert result.count("G12") == 2

    def test_no_inject_when_not_on_previous_page(self):
        """A one-off heading missing on a page (not a running header) is NOT injected."""
        text = (
            "<!-- page: 50 -->\n## 别的标题\n正文\n"
            "<!-- page: 51 -->\n正文，无标题\n"
        )
        ctx = _ctx(
            _ps(50, [_h("别的标题", 2)]),
            _ps(51, [_h("某节标题", 2)]),  # not present on page 50
        )
        result = inject_running_section_headers(text, ctx)
        p51 = result.split("<!-- page: 51 -->")[1]
        assert "## 某节标题" not in p51

    def test_toc_page_skipped(self):
        text = (
            "<!-- page: 2 -->\n## G12 端附工序工艺规程\n正文\n"
            "<!-- page: 3 -->\n目录内容\n"
        )
        ctx = _ctx(
            _ps(2, [_h("G12 端附工序工艺规程", 2)]),
            _ps(3, [_h("G12 端附工序工艺规程", 2)], is_toc=True),
        )
        result = inject_running_section_headers(text, ctx)
        p3 = result.split("<!-- page: 3 -->")[1]
        assert "## G12 端附工序工艺规程" not in p3

    def test_injects_on_section_start_page_when_next_page_has_it(self):
        """工序段落首页 VLM 漏写页眉，但下一页 P1 也有 → 视为运行页眉，补齐。"""
        text = (
            "<!-- page: 73 -->\n正文：本工序内容\n"
            "<!-- page: 74 -->\n## G10 烧结工序工艺规程\n正文续\n"
        )
        ctx = _ctx(
            _ps(72, [_h("G09 脱脂工序工艺规程", 2)]),       # 上一个工序
            _ps(73, [_h("G10 烧结工序工艺规程", 2)]),
            _ps(74, [_h("G10 烧结工序工艺规程", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        p73 = result.split("<!-- page: 73 -->")[1].split("<!-- page: 74 -->")[0]
        assert "## G10 烧结工序工艺规程" in p73

    def test_no_inject_for_single_page_heading(self):
        """仅单页出现、相邻页都没有的标题，不注入（避免误判一次性正文标题）。"""
        text = (
            "<!-- page: 40 -->\n正文a\n"
            "<!-- page: 41 -->\n正文b，无标题\n"
            "<!-- page: 42 -->\n正文c\n"
        )
        ctx = _ctx(
            _ps(40, [_h("某节A", 2)]),
            _ps(41, [_h("一次性小节标题", 2)]),  # 相邻页都没有
            _ps(42, [_h("某节C", 2)]),
        )
        result = inject_running_section_headers(text, ctx)
        p41 = result.split("<!-- page: 41 -->")[1].split("<!-- page: 42 -->")[0]
        assert "## 一次性小节标题" not in p41

    def test_inject_uses_p1_level(self):
        text = (
            "<!-- page: 91 -->\n### G12 端附工序工艺规程\n正文a\n"
            "<!-- page: 92 -->\n正文b\n"
        )
        ctx = _ctx(
            _ps(91, [_h("G12 端附工序工艺规程", 3)]),
            _ps(92, [_h("G12 端附工序工艺规程", 3)]),
        )
        result = inject_running_section_headers(text, ctx)
        p92 = result.split("<!-- page: 92 -->")[1]
        assert "### G12 端附工序工艺规程" in p92

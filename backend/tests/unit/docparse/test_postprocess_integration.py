"""Integration test for the full postprocess_markdown pipeline."""
from app.core.docparse.models import DocumentContext
from app.core.docparse.postprocess import postprocess_markdown


def _ctx(**kwargs) -> DocumentContext:
    defaults = dict(pdf_path="test.pdf", file_title="Test Doc", total_pages=2)
    defaults.update(kwargs)
    return DocumentContext(**defaults)


def test_pipeline_produces_single_h1():
    raw = "<!-- page: 1 -->\n# Test Doc\n## 1 项目概述\n内容"
    out = postprocess_markdown(raw, "Test Doc", _ctx(total_pages=1))
    h1_lines = [ln for ln in out.split('\n') if ln.startswith('# ') and not ln.startswith('## ')]
    assert len(h1_lines) == 1
    assert h1_lines[0] == "# Test Doc"


def test_pipeline_toc_normalized_and_body_preserved():
    raw = (
        "<!-- page: 1 -->\n## 目录\n## 1 项目概述 ...... 1\n"
        "<!-- page: 2 -->\n## 1 项目概述\n正文内容"
    )
    out = postprocess_markdown(raw, "Test Doc", _ctx(total_pages=2, toc_pages=[1]))
    assert "**目录**" in out
    assert "- 1 项目概述" in out
    # Body heading on page 2 should remain as ## (correct level for single-digit)
    assert "## 1 项目概述" in out


def test_pipeline_no_duplicate_title():
    raw = "<!-- page: 1 -->\n# Test Doc\n## 1 项目概述\n"
    out = postprocess_markdown(raw, "Test Doc", _ctx(total_pages=1))
    assert out.count("# Test Doc") == 1
    assert "## Test Doc" not in out


def test_pipeline_promotes_appendix_and_clears_redaction():
    raw = (
        "<!-- page: 1 -->\n"
        "### G01 配料工序工艺规程\n"
        "**附表1 配方**\n"
        "| 1 | <NOTSURE>/////</NOTSURE> |\n"
        "### 图1-1 示意图\n"
    )
    out = postprocess_markdown(raw, "Test Doc", _ctx(total_pages=1))
    assert "### 附表1 配方" in out
    assert "<NOTSURE>" not in out
    assert "**图1-1 示意图**" in out

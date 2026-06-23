from app.core.docparse.models import DocumentContext
from app.core.docparse.postprocess import postprocess_markdown, strip_output_noise


def test_strip_page_number_lines():
    inp = "正文\n第 2 页\n更多正文"
    out = strip_output_noise(inp)
    assert "第 2 页" not in out
    assert "正文" in out
    assert "更多正文" in out


def test_strip_html_page_footer_div():
    inp = '内容\n<div align="right">定型<br>第 1 页<br>共 8 页</div>\n后续'
    out = strip_output_noise(inp)
    assert "<div" not in out
    assert "第 1 页" not in out
    assert "后续" in out


def test_cover_doc_heading_preserved():
    inp = "# 设计文档\n\n## 工艺文件\n\n项目编号"
    out = strip_output_noise(inp)
    assert "## 工艺文件" in out


def test_pipeline_strips_noise():
    raw = (
        "<!-- page: 1 -->\n"
        "## 工艺文件\n"
        "正文\n"
        "第 2 页\n"
        '<div align="right">定型<br>第 1 页<br>共 8 页</div>'
    )
    ctx = DocumentContext(pdf_path="x.pdf", file_title="设计文档", total_pages=1)
    out = postprocess_markdown(raw, "设计文档", ctx)
    assert "第 2 页" not in out
    assert "<div" not in out
    assert "## 工艺文件" in out

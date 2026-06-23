from app.core.docparse.postprocess import normalize_toc_blocks


def test_converts_toc_headings_to_list():
    inp = "<!-- page: 1 -->\n## 目录\n## 1 项目概述 ...... 1\n### 1.1 建设背景 ...... 2\n## 附件1 高管名单 ...... 18"
    out = normalize_toc_blocks(inp, toc_pages=[1])
    assert "**目录**" in out
    assert "- 1 项目概述" in out
    assert "## 1 项目概述" not in out


def test_ignores_non_toc_pages():
    inp = "<!-- page: 2 -->\n## 1 项目概述\n正文内容"
    out = normalize_toc_blocks(inp, toc_pages=[1])
    assert "## 1 项目概述" in out

from app.core.docparse.postprocess import ensure_single_h1


def test_removes_duplicate_h1():
    inp = "# 文件名\n# 1 项目概述\n# 2 系统架构"
    out = ensure_single_h1(inp, "文件名")
    assert out.count("\n# ") == 0 or out.startswith("# 文件名\n\n## ")
    assert "## 1 项目概述" in out
    assert "## 2 系统架构" in out


def test_prepends_h1_when_missing():
    inp = "## 1 项目概述\n内容"
    out = ensure_single_h1(inp, "火炬电子")
    assert out.startswith("# 火炬电子")


def test_no_duplicate_title_when_h1_matches_file_title():
    # If the canonical H1 already matches file_title, it must NOT appear as ## too
    inp = "# 火炬电子\n## 1 项目概述\n内容"
    out = ensure_single_h1(inp, "火炬电子")
    assert out.startswith("# 火炬电子")
    assert "## 火炬电子" not in out

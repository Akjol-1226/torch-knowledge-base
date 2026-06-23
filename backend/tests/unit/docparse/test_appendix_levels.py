from app.core.docparse.postprocess import normalize_appendix_headings


def test_appendix_item_to_h3():
    inp = "## 附件1：高管名单"
    out = normalize_appendix_headings(inp)
    assert "### 附件1：高管名单" in out


def test_appendix_total_stays_h2():
    inp = "### 附件"
    out = normalize_appendix_headings(inp)
    assert "## 附件" in out


def test_appendix_sub_to_h4():
    inp = "## 附件1.1：字段说明"
    out = normalize_appendix_headings(inp)
    assert "#### 附件1.1：字段说明" in out

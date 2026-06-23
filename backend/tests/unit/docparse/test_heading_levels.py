from app.core.docparse.postprocess import fix_heading_level_inversions, fix_numbered_heading_levels


def test_fixes_1_1_from_h2_to_h3():
    inp = "## 1.1 建设背景"
    out = fix_numbered_heading_levels(inp)
    assert out.strip() == "### 1.1 建设背景"


def test_fixes_1_1_1_from_h2_to_h4():
    inp = "## 1.1.1 数据来源"
    out = fix_numbered_heading_levels(inp)
    assert out.strip() == "#### 1.1.1 数据来源"


def test_preserves_correct_h2():
    inp = "## 1 项目概述"
    out = fix_numbered_heading_levels(inp)
    assert out.strip() == "## 1 项目概述"


def test_skips_code_blocks():
    inp = "```\n## 1.1 not a heading\n```"
    out = fix_numbered_heading_levels(inp)
    assert out == inp


# ── fix_heading_level_inversions ──────────────────────────────────────────────

def test_inversion_arabic_child_shallower_than_parent():
    # 1.1 at H3 under 1. at H4 → 1.1 should become H5
    inp = "#### 1、产品概述\n### 1.1 产品信息来源"
    out = fix_heading_level_inversions(inp)
    lines = out.split('\n')
    assert lines[0] == "#### 1、产品概述"
    assert lines[1].startswith("#####")


def test_inversion_arabic_multiple_siblings_fixed():
    # Both 1.1 and 1.2 at H3 under 1. at H4
    inp = "#### 1、产品概述\n### 1.1 信息来源\n### 1.2 技术指标"
    out = fix_heading_level_inversions(inp)
    lines = out.split('\n')
    assert lines[1].startswith("#####"), f"1.1 should be H5, got: {lines[1]}"
    assert lines[2].startswith("#####"), f"1.2 should be H5, got: {lines[2]}"


def test_inversion_arabic_under_chinese_major_same_level():
    # 1. at same level as 一、 → 1. should be bumped one deeper
    inp = "### 一、立项必要性分析\n### 1、背景"
    out = fix_heading_level_inversions(inp)
    lines = out.split('\n')
    assert lines[0] == "### 一、立项必要性分析"
    assert lines[1].startswith("####"), f"1. should be H4, got: {lines[1]}"


def test_inversion_correct_levels_unchanged():
    # 6.1 at H5 under 6. at H4 — already correct, should not change
    inp = "#### 6、风险分析\n##### 6.1 技术风险\n##### 6.2 进度风险"
    out = fix_heading_level_inversions(inp)
    assert out == inp


def test_inversion_context_reset_at_h2():
    # H2 non-numbered heading resets context; 1. after it is unaffected
    inp = "## 项目输入评审记录\n#### 1、产品概述\n### 1.1 信息"
    out = fix_heading_level_inversions(inp)
    lines = out.split('\n')
    assert lines[0] == "## 项目输入评审记录"
    assert lines[1] == "#### 1、产品概述"
    assert lines[2].startswith("#####"), f"1.1 should be H5 after fix, got: {lines[2]}"


def test_inversion_code_block_skipped():
    inp = "#### 1、概述\n```\n### 1.1 inside code\n```"
    out = fix_heading_level_inversions(inp)
    assert "### 1.1 inside code" in out

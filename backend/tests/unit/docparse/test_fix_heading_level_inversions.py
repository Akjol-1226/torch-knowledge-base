from app.core.docparse.postprocess import fix_heading_level_inversions


def test_arabic_depth1_at_same_level_as_chinese_major_is_demoted():
    # 一、(H2) 后出现同级 H2 的 1、→ 降为 H3
    inp = "## 二、总则\n## 1、适用范围"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "## 二、总则"
    assert lines[1] == "### 1、适用范围"


def test_arabic_child_at_same_level_as_parent_is_demoted():
    # 1.(H2) 后出现同级 H2 的 1.1 → 降为 H3
    inp = "## 1、总则\n## 1.1 子节"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "## 1、总则"
    assert lines[1] == "### 1.1 子节"


def test_two_level_cascade_under_chinese_major():
    # 一、(H2) → 1.(H2→H3) → 1.1(H2→H4)
    inp = "## 一、总则\n## 1、标准\n## 1.1 详情"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "## 一、总则"
    assert lines[1] == "### 1、标准"
    assert lines[2] == "#### 1.1 详情"


def test_correctly_leveled_arabic_under_chinese_major_unchanged():
    # 一、(H2) 后 1.(H3) 已经正确，不改动
    inp = "## 一、总则\n### 1、标准"
    out = fix_heading_level_inversions(inp)
    assert out == inp


def test_code_block_content_not_modified():
    inp = "## 一、总则\n```\n## 1、代码块内容\n```"
    out = fix_heading_level_inversions(inp)
    assert "## 1、代码块内容" in out


def test_non_numbered_h2_resets_context():
    # 非编号 H2 出现后，后续 Arabic 标题不再被降级
    inp = "## 一、总则\n## 1、标准\n## 质量要求\n## 1、新章节"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[1] == "### 1、标准"   # 降级
    assert lines[2] == "## 质量要求"   # 重置上下文
    assert lines[3] == "## 1、新章节"  # 不再降级（上下文已重置）


def test_second_chinese_major_resets_arabic_depth_level():
    # 第二个中文大节出现时 arabic_depth_level 被清空，重新开始
    inp = "## 一、总则\n## 1、标准\n## 二、目的\n## 1、适用"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "## 一、总则"
    assert lines[1] == "### 1、标准"
    assert lines[2] == "## 二、目的"
    assert lines[3] == "### 1、适用"  # 新的中文大节后，重新降级


def test_h6_cap_arabic_under_h6_chinese_major():
    # 中文大节在 H6，Arabic depth-1 也在 H6，min(7,6)=6 → 保持 H6
    inp = "###### 一、总则\n###### 1、标准"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "###### 一、总则"
    assert lines[1] == "###### 1、标准"


def test_arabic_depth_dict_pruned_on_shallower_hit():
    # depth=1 被重新设置后，更深的 depth=2 条目应被清除
    # 验证方式：先建立 1→H3, 2→H4，再遇到新的 1(H2)→H3，
    # 然后 2.1(H2) 只参照当前 depth=1 的 H3，应降为 H4
    inp = "## 一、总则\n## 1、标准\n## 1.1 详情\n## 2、新节\n## 2.1 子节"
    out = fix_heading_level_inversions(inp)
    lines = out.splitlines()
    assert lines[0] == "## 一、总则"
    assert lines[1] == "### 1、标准"
    assert lines[2] == "#### 1.1 详情"
    assert lines[3] == "### 2、新节"
    assert lines[4] == "#### 2.1 子节"

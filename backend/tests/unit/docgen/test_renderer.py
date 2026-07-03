from app.modules.docgen.renderer import render_draft
from app.modules.docgen.schemas import ChangePlanItem, ChangeSourceType


def _item(
    item_id="chg_001",
    old_text="0805",
    new_text="1206",
    source_type=ChangeSourceType.user,
    selected=True,
):
    return ChangePlanItem(
        id=item_id,
        node_id="doc_a:0001",
        location="第 1 章",
        old_text=old_text,
        new_text=new_text,
        source_type=source_type,
        reason="测试",
        source_handles=["doc_a:0001"],
        selected=selected,
    )


def test_render_draft_applies_unique_replacement_and_highlights_user_change():
    result = render_draft(
        markdown="# 工艺\n\n0805 产品",
        title="工艺文件",
        items=[_item()],
    )

    assert result.markdown == "# 工艺\n\n1206 产品"
    assert '<mark class="docgen-change-user">1206</mark>' in result.highlighted_markdown
    assert result.copy_text == "工艺\n\n1206 产品"
    assert result.applied_item_ids == ["chg_001"]
    assert result.highlight_count == 1


def test_render_draft_skips_unselected_item():
    result = render_draft(
        markdown="# 工艺\n\n0805 产品",
        title="工艺文件",
        items=[_item(selected=False)],
    )

    assert result.markdown == "# 工艺\n\n0805 产品"
    assert result.applied_item_ids == []
    assert result.skipped_item_ids == ["chg_001"]


def test_render_draft_marks_missing_old_text_unresolved():
    result = render_draft(
        markdown="# 工艺\n\n0603 产品",
        title="工艺文件",
        items=[_item()],
    )

    assert result.markdown == "# 工艺\n\n0603 产品"
    assert result.unresolved_item_ids == ["chg_001"]
    assert "未找到原文" in result.warnings[0]


def test_render_draft_marks_ambiguous_old_text_unresolved():
    result = render_draft(
        markdown="0805 产品\n0805 型号",
        title="工艺文件",
        items=[_item()],
    )

    assert result.markdown == "0805 产品\n0805 型号"
    assert result.unresolved_item_ids == ["chg_001"]
    assert "出现多次" in result.warnings[0]


def test_render_draft_highlights_ai_change_differently():
    result = render_draft(
        markdown="额定电容 220 pF",
        title="工艺文件",
        items=[_item(old_text="220 pF", new_text="470 pF", source_type=ChangeSourceType.ai)],
    )

    assert '<mark class="docgen-change-ai">470 pF</mark>' in result.highlighted_markdown


def test_render_draft_highlights_html_table_cell_without_breaking_table_structure():
    result = render_draft(
        markdown="<table><tr><td>G-4161</td><td></td></tr></table>",
        title="宸ヨ壓鏂囦欢",
        items=[
            _item(
                old_text="<td>G-4161</td>",
                new_text="<td>111111</td>",
            )
        ],
    )

    assert result.markdown == "<table><tr><td>111111</td><td></td></tr></table>"
    assert (
        '<td><mark class="docgen-change-user">111111</mark></td>'
        in result.highlighted_markdown
    )
    assert (
        '<mark class="docgen-change-user">&lt;td&gt;111111&lt;/td&gt;</mark>'
        not in result.highlighted_markdown
    )

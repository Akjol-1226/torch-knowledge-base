import pytest
from pydantic import ValidationError

from app.modules.docgen.schemas import ChangePlanItem, ChangeSourceType


def test_change_plan_item_sets_display_label_for_user_source():
    item = ChangePlanItem(
        id="chg_001",
        node_id="doc_a:0001",
        location="第 1 章",
        old_text="0805",
        new_text="1206",
        source_type=ChangeSourceType.user,
        reason="用户明确指定",
        source_handles=["doc_a:0001"],
    )

    assert item.source_label == "你指定"
    assert item.selected is True


def test_change_plan_item_sets_ai_warning_defaults():
    item = ChangePlanItem(
        id="chg_002",
        node_id="doc_a:0002",
        location="附表 1",
        old_text="220 pF",
        new_text="470 pF",
        source_type=ChangeSourceType.ai,
        reason="按用户要求推算",
        source_handles=["doc_a:0002"],
    )

    assert item.source_label == "AI推理"
    assert item.warning == "AI 推理值，必须人工核对。"


def test_change_plan_item_requires_source_handles():
    with pytest.raises(ValidationError):
        ChangePlanItem(
            id="chg_003",
            node_id="doc_a:0003",
            location="第 2 章",
            old_text="x",
            new_text="y",
            source_type=ChangeSourceType.user,
            reason="用户指定",
            source_handles=[],
        )

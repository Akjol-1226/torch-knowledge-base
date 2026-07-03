import pytest

from app.modules.docgen.agent import DocgenAgentError, extract_json_object


def test_extract_json_object_from_fenced_json():
    payload = extract_json_object('```json\n{"summary":"蓝本","question":"做什么？"}\n```')

    assert payload["summary"] == "蓝本"


def test_extract_json_object_rejects_non_json_text():
    with pytest.raises(DocgenAgentError):
        extract_json_object("不是 JSON")

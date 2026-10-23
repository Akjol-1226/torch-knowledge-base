import json

import pytest

from app.modules.chat.sse import autofix_grounding, events_from_runtime, extract_cite


async def _stream(events):
    for event in events:
        yield event


async def _collect(events, seed_cites=None):
    return [
        event
        async for event in events_from_runtime(_stream(events), seed_cites=seed_cites)
    ]


def _chunk(text, round_index=1):
    return {"type": "assistant_chunk", "round": round_index, "text": text}


def _complete(has_tool_calls=False, round_index=1):
    return {
        "type": "assistant_complete",
        "round": round_index,
        "has_tool_calls": has_tool_calls,
    }


def _call(name, args=None, call_id="call-1", round_index=1):
    return {
        "type": "tool_call",
        "round": round_index,
        "call_id": call_id,
        "name": name,
        "args": args or {},
    }


def _result(name, payload, *, is_error=False, call_id="call-1", round_index=1):
    return {
        "type": "tool_result",
        "round": round_index,
        "call_id": call_id,
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False),
        "is_error": is_error,
    }


def _cite(handle="doc_a:0003", doc_id="doc_a", doc="工艺文件", section="5.3 回流焊"):
    return {
        "doc": doc,
        "section": section,
        "lines": "8-14",
        "handle": handle,
        "doc_id": doc_id,
        "snippet": "峰值245℃",
    }


def test_extract_cite_returns_structured_dict_from_read_node():
    cite = _cite()
    assert extract_cite(json.dumps({"cite": cite}, ensure_ascii=False)) == [cite]


def test_extract_cite_from_search_hits_list():
    payload = [{"cite": _cite("d:1", "d")}, {"cite": _cite("d:2", "d")}]
    assert [cite["handle"] for cite in extract_cite(payload)] == ["d:1", "d:2"]


@pytest.mark.asyncio
async def test_runtime_events_map_to_existing_frontend_contract():
    cite = _cite()
    events = await _collect(
        [
            _call("read_node", {"node_id": "doc_a:0003"}),
            _complete(True),
            _result("read_node", {"cite": cite}),
            _chunk("峰值", 2),
            _chunk("245℃", 2),
            _complete(False, 2),
        ]
    )

    assert [event["type"] for event in events] == [
        "tool",
        "tool_result",
        "chunk",
        "chunk",
        "answer",
    ]
    assert events[0] == {
        "type": "tool",
        "name": "read_node",
        "args": {"node_id": "doc_a:0003"},
    }
    assert events[-1]["text"] == "峰值245℃"
    assert events[-1]["sources"][0]["nodes"][0]["handle"] == "doc_a:0003"


@pytest.mark.asyncio
async def test_tool_decision_round_text_is_not_part_of_final_answer():
    events = await _collect(
        [
            _chunk("我先检索。"),
            _call("search_nodes", {"query": "峰值"}),
            _complete(True),
            _result("search_nodes", []),
            _chunk("最终答案", 2),
            _complete(False, 2),
        ]
    )

    assert [event["text"] for event in events if event["type"] == "chunk"] == [
        "我先检索。",
        "最终答案",
    ]
    assert events[-1]["text"] == "最终答案"


@pytest.mark.asyncio
async def test_read_node_source_is_only_returned_with_final_answer():
    events = await _collect(
        [
            _result("read_node", {"cite": _cite()}),
            _chunk("峰值 245℃[[cite:doc_a:0003]]"),
            _complete(),
        ]
    )

    assert [event["type"] for event in events] == ["tool_result", "chunk", "answer"]
    assert not [event for event in events if event["type"] == "source"]
    assert events[-1]["sources"][0]["nodes"][0]["handle"] == "doc_a:0003"


@pytest.mark.asyncio
async def test_sources_group_by_doc_and_deduplicate_handle():
    events = await _collect(
        [
            _result("read_node", {"cite": _cite("doc_a:0003")}, call_id="1"),
            _result(
                "read_node",
                {"cite": _cite("doc_a:0007", section="6 检验")},
                call_id="2",
            ),
            _result(
                "read_node",
                {"cite": _cite("doc_b:0001", "doc_b", "鉴定报告", "结论")},
                call_id="3",
            ),
            _result("read_node", {"cite": _cite("doc_a:0003")}, call_id="4"),
        ]
    )
    sources = events[-1]["sources"]

    assert [group["doc_id"] for group in sources] == ["doc_a", "doc_b"]
    assert [node["handle"] for node in sources[0]["nodes"]] == [
        "doc_a:0003",
        "doc_a:0007",
    ]
    assert [node["handle"] for node in sources[1]["nodes"]] == ["doc_b:0001"]


@pytest.mark.asyncio
async def test_search_and_error_results_do_not_enter_sources():
    events = await _collect(
        [
            _result("search_nodes", [{"cite": _cite()}]),
            _result("read_node", {"cite": _cite("doc_a:0007")}, is_error=True),
            _chunk("结论[[cite:doc_a:0003]][[cite:doc_a:0007]]"),
            _complete(),
        ]
    )
    answer = events[-1]

    assert answer["sources"] == []
    assert answer["ungrounded"] == ["doc_a:0003", "doc_a:0007"]


@pytest.mark.asyncio
async def test_cite_without_handle_falls_back_gracefully():
    cite = {"doc": "D", "section": "S", "lines": "1-2"}
    events = await _collect(
        [
            _result("read_node", {"cite": cite}, call_id="1"),
            _result("read_node", {"cite": cite}, call_id="2"),
        ]
    )
    sources = events[-1]["sources"]

    assert len(sources) == 1 and sources[0]["doc_id"] == "D"
    assert len(sources[0]["nodes"]) == 1


@pytest.mark.asyncio
async def test_answer_reports_ungrounded_citation():
    events = await _collect([_chunk("结论[[cite:doc_x:0009]]"), _complete()])

    assert events[-1]["ungrounded"] == ["doc_x:0009"]
    assert events[-1]["sources"] == []


@pytest.mark.asyncio
async def test_seed_cites_marks_node_as_grounded():
    seed = [
        {
            "doc": "工艺",
            "section": "S",
            "lines": "1-2",
            "handle": "doc_x:0009",
            "doc_id": "doc_x",
        }
    ]
    events = await _collect(
        [_chunk("结论[[cite:doc_x:0009]]"), _complete()], seed_cites=seed
    )

    assert events[-1]["ungrounded"] == []
    assert events[-1]["sources"][0]["nodes"][0]["handle"] == "doc_x:0009"


def test_autofix_grounding_adds_readable_sources_and_drops_missing(monkeypatch):
    import app.modules.chat.tools as tools_mod

    class FakeStore:
        def read_node(self, handle):
            if handle == "doc_x:0001":
                return {
                    "title": "流程图",
                    "text": "节点列表：J01 进料检验判定……",
                    "cite": {
                        "doc": "工艺",
                        "section": "流程图",
                        "lines": "1-9",
                        "handle": handle,
                        "doc_id": "doc_x",
                    },
                }
            return {"error": "not found"}

    monkeypatch.setattr(tools_mod, "get_store", lambda: FakeStore())
    event = {
        "type": "answer",
        "text": "已读[[cite:doc_x:0001]]，缺失[[cite:doc_x:9999]]",
        "sources": [],
        "ungrounded": ["doc_x:0001", "doc_x:9999"],
        "read_cites": [],
    }

    fixed = autofix_grounding(event, event["ungrounded"])

    assert fixed is not None
    assert "[[cite:doc_x:0001]]" in fixed["text"]
    assert "[[cite:doc_x:9999]]" not in fixed["text"]
    assert fixed["sources"][0]["nodes"][0]["handle"] == "doc_x:0001"


def test_autofix_grounding_declines_many_ungrounded_ids():
    event = {"type": "answer", "text": "", "sources": [], "read_cites": []}
    assert autofix_grounding(event, ["a:1", "a:2", "a:3", "a:4"]) is None


def test_build_grounding_correction(monkeypatch):
    import app.modules.chat.tools as tools_mod
    from app.modules.chat.sse import build_grounding_correction

    class FakeStore:
        def read_node(self, handle):
            if handle == "doc_x:0001":
                return {
                    "title": "流程图",
                    "text": "节点列表：J01 进料检验判定……",
                    "cite": {
                        "doc": "工艺",
                        "section": "流程图",
                        "lines": "1-9",
                        "handle": handle,
                        "doc_id": "doc_x",
                    },
                }
            return {"error": "not found"}

    monkeypatch.setattr(tools_mod, "get_store", lambda: FakeStore())
    seed, text = build_grounding_correction(["doc_x:0001", "doc_x:9999"])

    assert [cite["handle"] for cite in seed] == ["doc_x:0001"]
    assert "节点列表" in text
    assert "doc_x:9999" in text and "读不到" in text

import pytest

from app.modules.chat import execution


def _cite(handle: str) -> dict:
    return {
        "doc": "工艺文件",
        "section": handle,
        "lines": "1-2",
        "handle": handle,
        "doc_id": "doc_a",
    }


class ScriptedRuntime:
    backend = "native"

    def __init__(self, streams):
        self.streams = streams
        self.inputs = []

    async def astream(self, state, config=None):
        self.inputs.append(state)
        for event in self.streams.pop(0):
            yield event


@pytest.mark.asyncio
async def test_execute_turn_rewrites_ungrounded_answer_and_preserves_old_sources(monkeypatch):
    old = _cite("doc_a:0001")
    repaired = _cite("doc_a:0002")
    runtime = ScriptedRuntime(
        [
            [
                {
                    "type": "tool_result",
                    "round": 1,
                    "call_id": "call-1",
                    "name": "read_node",
                    "content": {"cite": old},
                    "is_error": False,
                },
                {
                    "type": "assistant_chunk",
                    "round": 2,
                    "text": "初稿[[cite:doc_a:0001]][[cite:doc_a:0002]]",
                },
                {"type": "assistant_complete", "round": 2, "has_tool_calls": False},
            ],
            [
                {
                    "type": "assistant_chunk",
                    "round": 1,
                    "text": "修订稿[[cite:doc_a:0001]][[cite:doc_a:0002]]",
                },
                {"type": "assistant_complete", "round": 1, "has_tool_calls": False},
            ],
        ]
    )
    monkeypatch.setattr(execution, "autofix_grounding", lambda event, ids: None)
    monkeypatch.setattr(
        execution,
        "build_grounding_correction",
        lambda ids: ([repaired], "请按补读正文纠正"),
    )

    events = [
        event
        async for event in execution.execute_turn(
            runtime,
            [{"role": "user", "content": "问题"}],
            {"max_concurrency": 3},
        )
    ]

    assert len(runtime.inputs) == 2
    assert runtime.inputs[1]["messages"][-1]["content"] == "请按补读正文纠正"
    assert [event["text"] for event in events if event["type"] == "chunk"] == [
        "初稿[[cite:doc_a:0001]][[cite:doc_a:0002]]"
    ]
    repair_event = [event for event in events if event.get("origin") == "grounding_repair"]
    assert repair_event[0]["read_count"] == 1
    answer = events[-1]
    assert answer["text"].startswith("修订稿")
    assert [node["handle"] for group in answer["sources"] for node in group["nodes"]] == [
        "doc_a:0001",
        "doc_a:0002",
    ]

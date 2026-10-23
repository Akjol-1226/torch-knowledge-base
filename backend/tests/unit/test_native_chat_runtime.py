import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.tools import tool

from app.modules.chat.native_runtime import (
    AgentProtocolError,
    AgentStepLimitError,
    NativeReactRuntime,
)


@tool
def echo(value: str) -> dict:
    """Return the provided value."""
    return {"value": value}


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.inputs = []

    def bind_tools(self, tools, **kwargs):
        self.tools = tools
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.append(list(messages))
        return self.responses.pop(0)

    async def astream(self, messages, config=None):
        self.inputs.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, list):
            for chunk in response:
                yield chunk
        else:
            yield AIMessageChunk(content=response.content, tool_calls=response.tool_calls)


def _tool_call(value="hello", *, name="echo", call_id="call-1"):
    return {
        "name": name,
        "args": {"value": value},
        "id": call_id,
        "type": "tool_call",
    }


@pytest.mark.asyncio
async def test_native_runtime_returns_answer_without_tools():
    runtime = NativeReactRuntime(ScriptedModel([AIMessage(content="完成")]), [echo], "system")

    result = await runtime.ainvoke({"messages": [{"role": "user", "content": "测试"}]})

    assert result["messages"][-1].content == "完成"


@pytest.mark.asyncio
async def test_native_runtime_runs_tool_loop_and_preserves_call_id():
    model = ScriptedModel(
        [AIMessage(content="", tool_calls=[_tool_call()]), AIMessage(content="完成")]
    )
    runtime = NativeReactRuntime(model, [echo], "system")

    result = await runtime.ainvoke({"messages": [{"role": "user", "content": "测试"}]})

    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert json.loads(tool_messages[0].content) == {"value": "hello"}
    assert tool_messages[0].tool_call_id == "call-1"
    assert result["messages"][-1].content == "完成"


@pytest.mark.asyncio
async def test_native_runtime_stream_emits_owned_events_in_order():
    model = ScriptedModel(
        [AIMessage(content="", tool_calls=[_tool_call()]), AIMessage(content="最终答案")]
    )
    runtime = NativeReactRuntime(model, [echo], "system")

    events = [
        event
        async for event in runtime.astream(
            {"messages": [{"role": "user", "content": "测试"}]}
        )
    ]

    assert [event["type"] for event in events] == [
        "tool_call",
        "assistant_complete",
        "tool_result",
        "assistant_chunk",
        "assistant_complete",
    ]
    assert events[0]["call_id"] == "call-1"
    assert events[2]["call_id"] == "call-1"
    assert events[3]["text"] == "最终答案"


@pytest.mark.asyncio
async def test_native_runtime_keeps_parallel_tool_results_in_call_order():
    finished = []

    @tool
    async def delayed(value: str) -> str:
        """Return after a value-dependent delay."""
        await asyncio.sleep(0.02 if value == "first" else 0)
        finished.append(value)
        return value

    calls = [
        _tool_call("first", name="delayed", call_id="call-1"),
        _tool_call("second", name="delayed", call_id="call-2"),
    ]
    runtime = NativeReactRuntime(
        ScriptedModel([AIMessage(content="", tool_calls=calls), AIMessage(content="完成")]),
        [delayed],
        "system",
    )

    events = [event async for event in runtime.astream({"messages": []})]
    results = [event for event in events if event["type"] == "tool_result"]

    assert finished == ["second", "first"]
    assert [event["call_id"] for event in results] == ["call-1", "call-2"]


@pytest.mark.asyncio
async def test_native_runtime_unknown_tool_returns_error_result():
    runtime = NativeReactRuntime(
        ScriptedModel(
            [
                AIMessage(content="", tool_calls=[_tool_call(name="missing")]),
                AIMessage(content="完成"),
            ]
        ),
        [echo],
        "system",
    )

    events = [event async for event in runtime.astream({"messages": []})]
    result = next(event for event in events if event["type"] == "tool_result")

    assert result["is_error"] is True
    assert result["call_id"] == "call-1"


@pytest.mark.asyncio
async def test_native_runtime_assigns_one_stable_id_when_model_omits_it():
    call = _tool_call()
    call["id"] = None
    model = ScriptedModel(
        [AIMessage(content="", tool_calls=[call]), AIMessage(content="完成")]
    )
    runtime = NativeReactRuntime(
        model,
        [echo],
        "system",
    )

    events = [event async for event in runtime.astream({"messages": []})]
    tool_call = next(event for event in events if event["type"] == "tool_call")
    tool_result = next(event for event in events if event["type"] == "tool_result")

    assert tool_call["call_id"]
    assert tool_result["call_id"] == tool_call["call_id"]
    assistant = next(
        message
        for message in model.inputs[1]
        if isinstance(message, AIMessage) and message.tool_calls
    )
    tool_message = next(
        message for message in model.inputs[1] if isinstance(message, ToolMessage)
    )
    assert assistant.tool_calls[0]["id"] == tool_call["call_id"]
    assert tool_message.tool_call_id == tool_call["call_id"]


@pytest.mark.asyncio
async def test_native_runtime_ainvoke_writes_generated_call_id_back_to_history():
    call = _tool_call()
    call["id"] = None
    model = ScriptedModel(
        [AIMessage(content="", tool_calls=[call]), AIMessage(content="完成")]
    )
    runtime = NativeReactRuntime(model, [echo], "system")

    await runtime.ainvoke({"messages": []})

    assistant = next(
        message
        for message in model.inputs[1]
        if isinstance(message, AIMessage) and message.tool_calls
    )
    tool_message = next(
        message for message in model.inputs[1] if isinstance(message, ToolMessage)
    )
    assert assistant.tool_calls[0]["id"]
    assert assistant.tool_calls[0]["id"] == tool_message.tool_call_id


@pytest.mark.asyncio
async def test_native_runtime_rejects_invalid_nonstream_tool_call():
    invalid = {
        "name": "echo",
        "args": "{bad-json",
        "id": "bad-call",
        "error": "invalid json",
    }
    runtime = NativeReactRuntime(
        ScriptedModel([AIMessage(content="", invalid_tool_calls=[invalid])]),
        [echo],
        "system",
    )

    with pytest.raises(AgentProtocolError, match="invalid tool calls"):
        await runtime.ainvoke({"messages": []})


@pytest.mark.asyncio
async def test_native_runtime_rejects_invalid_streamed_tool_call():
    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"name": "echo", "args": "not-json", "id": "bad-call", "index": 0}
            ],
        )
    ]
    runtime = NativeReactRuntime(ScriptedModel([chunks]), [echo], "system")

    with pytest.raises(AgentProtocolError, match="invalid tool calls"):
        _ = [event async for event in runtime.astream({"messages": []})]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_duplicate_tool_call_ids_are_rejected_before_any_tool_runs(streaming):
    executed = []

    @tool
    def counted(value: str) -> dict:
        """Record whether the tool was executed."""
        executed.append(value)
        return {"value": value}

    duplicate_calls = [
        _tool_call("first", name="counted", call_id="duplicate"),
        _tool_call("second", name="counted", call_id="duplicate"),
    ]
    runtime = NativeReactRuntime(
        ScriptedModel([AIMessage(content="", tool_calls=duplicate_calls)]), [counted], "system"
    )

    with pytest.raises(AgentProtocolError, match="duplicate tool call ids"):
        if streaming:
            _ = [event async for event in runtime.astream({"messages": []})]
        else:
            await runtime.ainvoke({"messages": []})
    assert executed == []


@pytest.mark.asyncio
async def test_ainvoke_and_astream_feed_equivalent_tool_history_to_next_round():
    calls = [_tool_call("ok", call_id="call-ok"), _tool_call(name="missing", call_id="call-bad")]
    invoke_model = ScriptedModel(
        [AIMessage(content="", tool_calls=calls), AIMessage(content="最终答案")]
    )
    stream_model = ScriptedModel(
        [AIMessage(content="", tool_calls=calls), AIMessage(content="最终答案")]
    )
    invoke_runtime = NativeReactRuntime(invoke_model, [echo], "system")
    stream_runtime = NativeReactRuntime(stream_model, [echo], "system")

    invoke_result = await invoke_runtime.ainvoke({"messages": []})
    stream_events = [event async for event in stream_runtime.astream({"messages": []})]

    def tool_history(messages):
        return [
            (message.name, message.tool_call_id, message.status, message.content)
            for message in messages
            if isinstance(message, ToolMessage)
        ]

    assert len(invoke_model.inputs) == len(stream_model.inputs) == 2
    assert tool_history(invoke_model.inputs[1]) == tool_history(stream_model.inputs[1])
    assert invoke_result["messages"][-1].content == "最终答案"
    assert "".join(
        event["text"] for event in stream_events if event["type"] == "assistant_chunk"
    ) == "最终答案"


@pytest.mark.asyncio
async def test_native_runtime_validation_error_is_safe():
    runtime = NativeReactRuntime(
        ScriptedModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "echo",
                            "args": {"wrong": "value"},
                            "id": "bad-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="完成"),
            ]
        ),
        [echo],
        "system",
    )

    events = [event async for event in runtime.astream({"messages": []})]
    result = next(event for event in events if event["type"] == "tool_result")

    assert result["is_error"] is True
    assert result["content"] == "工具执行失败，请检查参数后重试。"
    assert "wrong" not in result["content"]


@pytest.mark.asyncio
async def test_native_runtime_tool_exception_does_not_leak_details():
    @tool
    def broken(value: str) -> str:
        """Raise a sensitive internal error."""
        raise RuntimeError(f"secret-path/C:/private/{value}")

    runtime = NativeReactRuntime(
        ScriptedModel(
            [
                AIMessage(content="", tool_calls=[_tool_call(name="broken")]),
                AIMessage(content="完成"),
            ]
        ),
        [broken],
        "system",
    )

    events = [event async for event in runtime.astream({"messages": []})]
    result = next(event for event in events if event["type"] == "tool_result")

    assert result["is_error"] is True
    assert "secret-path" not in result["content"]


@pytest.mark.asyncio
async def test_native_runtime_enforces_round_limit_without_executing_last_tool():
    executed = []

    @tool
    def counted(value: str) -> dict:
        """Record an invocation."""
        executed.append(value)
        return {"value": value}

    call = _tool_call(name="counted")
    runtime = NativeReactRuntime(
        ScriptedModel([AIMessage(content="", tool_calls=[call])]),
        [counted],
        "system",
        max_agent_rounds=1,
    )

    with pytest.raises(AgentStepLimitError):
        await runtime.ainvoke({"messages": []})
    assert executed == []


@pytest.mark.asyncio
async def test_native_runtime_empty_stream_raises_stable_error():
    runtime = NativeReactRuntime(ScriptedModel([[]]), [echo], "system")

    with pytest.raises(RuntimeError, match="empty stream"):
        _ = [event async for event in runtime.astream({"messages": []})]


@pytest.mark.asyncio
async def test_native_runtime_aggregates_fragmented_tool_call_chunks():
    fragments = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "echo", "args": '{"val', "id": "call-x", "index": 0}],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": None, "args": 'ue":"ok"}', "id": None, "index": 0}],
        ),
    ]
    runtime = NativeReactRuntime(
        ScriptedModel([fragments, AIMessage(content="完成")]), [echo], "system"
    )

    events = [event async for event in runtime.astream({"messages": []})]
    call = next(event for event in events if event["type"] == "tool_call")
    result = next(event for event in events if event["type"] == "tool_result")

    assert call["call_id"] == "call-x"
    assert call["args"] == {"value": "ok"}
    assert result["is_error"] is False

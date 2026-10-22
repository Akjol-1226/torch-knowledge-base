"""项目自有 Agent Runtime 事件协议。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class AssistantChunkEvent(TypedDict):
    type: Literal["assistant_chunk"]
    round: int
    text: str


class ToolCallEvent(TypedDict):
    type: Literal["tool_call"]
    round: int
    call_id: str
    name: str
    args: dict[str, Any]


class ToolResultEvent(TypedDict):
    type: Literal["tool_result"]
    round: int
    call_id: str
    name: str
    content: Any
    is_error: bool


class AssistantCompleteEvent(TypedDict):
    type: Literal["assistant_complete"]
    round: int
    has_tool_calls: bool


RuntimeEvent = (
    AssistantChunkEvent | ToolCallEvent | ToolResultEvent | AssistantCompleteEvent
)


def assistant_chunk(round_index: int, text: str) -> AssistantChunkEvent:
    return {"type": "assistant_chunk", "round": round_index, "text": text}


def tool_call(
    round_index: int,
    call_id: str,
    name: str,
    args: dict[str, Any],
) -> ToolCallEvent:
    return {
        "type": "tool_call",
        "round": round_index,
        "call_id": call_id,
        "name": name,
        "args": args,
    }


def tool_result(
    round_index: int,
    call_id: str,
    name: str,
    content: Any,
    *,
    is_error: bool,
) -> ToolResultEvent:
    return {
        "type": "tool_result",
        "round": round_index,
        "call_id": call_id,
        "name": name,
        "content": content,
        "is_error": is_error,
    }


def assistant_complete(
    round_index: int, *, has_tool_calls: bool
) -> AssistantCompleteEvent:
    return {
        "type": "assistant_complete",
        "round": round_index,
        "has_tool_calls": has_tool_calls,
    }

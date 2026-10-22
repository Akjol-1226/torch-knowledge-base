"""项目自研的轻量 ReAct Agent Harness。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    ToolMessage,
    convert_to_messages,
    message_chunk_to_message,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from app.core.logging import get_logger
from app.modules.chat.runtime_events import (
    RuntimeEvent,
    assistant_chunk,
    assistant_complete,
    tool_call,
    tool_result,
)

log = get_logger("chat.native_runtime")


class AgentStepLimitError(RuntimeError):
    """模型持续调用工具，超过本轮允许的最大模型轮数。"""


class AgentProtocolError(RuntimeError):
    """模型返回了无法安全执行的工具调用协议。"""


class NativeReactRuntime:
    """使用 LangChain 消息和工具协议实现的项目自有 ReAct 循环。"""

    backend = "native"

    def __init__(
        self,
        model: Any,
        tools: Sequence[BaseTool],
        system_prompt: str,
        *,
        max_agent_rounds: int = 25,
        max_tool_concurrency: int = 8,
    ) -> None:
        self._bound_model = model.bind_tools(list(tools))
        self._tools = {tool.name: tool for tool in tools}
        self._system_prompt = system_prompt
        self._max_agent_rounds = max(1, max_agent_rounds)
        self._max_tool_concurrency = max(1, max_tool_concurrency)

    def _messages(self, state: dict) -> list[BaseMessage]:
        raw = state.get("messages") or []
        return [SystemMessage(content=self._system_prompt), *convert_to_messages(raw)]

    @staticmethod
    def _tool_content(result: Any) -> str:
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    @staticmethod
    def _normalize_calls(calls: list[dict]) -> list[dict]:
        normalized = []
        for call in calls:
            normalized.append({**call, "id": str(call.get("id") or uuid.uuid4().hex)})
        return normalized

    def _prepare_reply(self, reply: Any) -> tuple[Any, list[dict]]:
        if getattr(reply, "invalid_tool_calls", None):
            raise AgentProtocolError("chat model returned invalid tool calls")
        calls = self._normalize_calls(list(getattr(reply, "tool_calls", None) or []))
        if len({call["id"] for call in calls}) != len(calls):
            raise AgentProtocolError("chat model returned duplicate tool call ids")
        if calls:
            # ToolMessage 的 call_id 必须能在上一条 AIMessage 中找到同一个 tool call id。
            reply = reply.model_copy(update={"tool_calls": calls})
        return reply, calls

    async def _run_tool(
        self,
        call: dict,
        config: RunnableConfig | None,
        semaphore: asyncio.Semaphore,
    ) -> ToolMessage:
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or uuid.uuid4().hex)
        tool = self._tools.get(name)
        if tool is None:
            return ToolMessage(
                content="未知工具，无法执行。",
                tool_call_id=call_id,
                name=name or None,
                status="error",
            )
        try:
            async with semaphore:
                result = await tool.ainvoke(
                    {**call, "id": call_id, "type": "tool_call"}, config=config
                )
            if isinstance(result, ToolMessage):
                return result
            return ToolMessage(
                content=self._tool_content(result),
                tool_call_id=call_id,
                name=name,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("chat_tool_failed", tool=name, call_id=call_id)
            return ToolMessage(
                content="工具执行失败，请检查参数后重试。",
                tool_call_id=call_id,
                name=name,
                status="error",
            )

    async def _run_tools(
        self, calls: list[dict], config: RunnableConfig | None
    ) -> list[ToolMessage]:
        configured = int((config or {}).get("max_concurrency") or self._max_tool_concurrency)
        semaphore = asyncio.Semaphore(max(1, configured))
        return list(
            await asyncio.gather(
                *(self._run_tool(call, config, semaphore) for call in calls)
            )
        )

    async def ainvoke(
        self, state: dict, config: RunnableConfig | None = None, **_: Any
    ) -> dict:
        messages = self._messages(state)
        for round_index in range(1, self._max_agent_rounds + 1):
            reply = await self._bound_model.ainvoke(messages, config=config)
            reply, calls = self._prepare_reply(reply)
            messages.append(reply)
            if not calls:
                return {"messages": messages[1:]}
            if round_index >= self._max_agent_rounds:
                raise AgentStepLimitError("native agent reached chat_max_agent_rounds")
            messages.extend(await self._run_tools(calls, config))
        raise AgentStepLimitError("native agent reached chat_max_agent_rounds")

    async def astream(
        self,
        state: dict,
        config: RunnableConfig | None = None,
        **_: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        messages = self._messages(state)
        for round_index in range(1, self._max_agent_rounds + 1):
            aggregate = None
            async for chunk in self._bound_model.astream(messages, config=config):
                aggregate = chunk if aggregate is None else aggregate + chunk
                text = getattr(chunk, "content", None)
                if isinstance(text, str) and text:
                    yield assistant_chunk(round_index, text)
            if aggregate is None:
                raise RuntimeError("chat model returned an empty stream")

            reply = message_chunk_to_message(aggregate)
            reply, calls = self._prepare_reply(reply)
            messages.append(reply)
            for call in calls:
                raw_args = call.get("args")
                args = raw_args if isinstance(raw_args, dict) else {}
                yield tool_call(
                    round_index,
                    str(call["id"]),
                    str(call.get("name") or ""),
                    args,
                )
            yield assistant_complete(round_index, has_tool_calls=bool(calls))
            if not calls:
                return
            if round_index >= self._max_agent_rounds:
                raise AgentStepLimitError("native agent reached chat_max_agent_rounds")

            results = await self._run_tools(calls, config)
            messages.extend(results)
            for call, result in zip(calls, results, strict=True):
                yield tool_result(
                    round_index,
                    str(call.get("id") or result.tool_call_id),
                    str(call.get("name") or result.name or ""),
                    result.content,
                    is_error=result.status == "error",
                )
        raise AgentStepLimitError("native agent reached chat_max_agent_rounds")

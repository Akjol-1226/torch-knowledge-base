"""项目自研的轻量 ReAct Agent Harness。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from time import perf_counter
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
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.logging import get_logger
from app.modules.chat.observation import (
    TurnObservation,
    grounding_read,
    invalidate,
    model_input,
    observe,
    snapshot,
    tool_outcome,
)
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
        self._model = model

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
        observation: TurnObservation | None = None,
        repair_pass: int = 0,
        round_index: int = 0,
    ) -> ToolMessage:
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or uuid.uuid4().hex)
        tool = self._tools.get(name)
        identity = {"repair_pass": repair_pass, "round_index": round_index,
                    "tool_call_id": call_id, "tool_name": name}
        queued_at = perf_counter()
        started_at = None
        result = None
        reason = None
        cancelled = False
        observe(observation, "tool_queued", **identity)
        try:
            if tool is None:
                reason = "unknown_tool"
                result = ToolMessage(
                    content="未知工具，无法执行。", tool_call_id=call_id,
                    name=name or None, status="error",
                )
                return result
            async with semaphore:
                started_at = perf_counter()
                observe(observation, "tool_started", **identity,
                        semaphore_wait_ms=(started_at - queued_at) * 1000)
                raw_result = await tool.ainvoke(
                    {**call, "id": call_id, "type": "tool_call"}, config=config
                )
            result = raw_result if isinstance(raw_result, ToolMessage) else ToolMessage(
                content=self._tool_content(raw_result), tool_call_id=call_id, name=name,
            )
            return result
        except asyncio.CancelledError:
            cancelled = True
            reason = "cancelled"
            raise
        except Exception:
            log.exception("chat_tool_failed", tool=name, call_id=call_id)
            reason = "invoke_error"
            result = ToolMessage(
                content="工具执行失败，请检查参数后重试。",
                tool_call_id=call_id,
                name=name,
                status="error",
            )
            return result
        finally:
            if observation is not None:
                self._observe_tool_finish(
                    observation, identity, result, queued_at, started_at, cancelled, reason, call
                )

    @staticmethod
    def _observe_tool_finish(
        observation: TurnObservation, identity: dict, result: ToolMessage | None,
        queued_at: float, started_at: float | None, cancelled: bool,
        reason: str | None, call: dict,
    ) -> None:
        try:
            finished_at = perf_counter()
            content = result.content if result is not None else None
            transport, domain = tool_outcome(
                content, is_error=result is None or result.status == "error"
            )
            observe(
                observation, "tool_finished", **identity,
                transport_status="cancelled" if cancelled else transport,
                domain_status="unknown" if cancelled else domain, reason=reason,
                invoke_wall_ms=(finished_at - started_at) * 1000 if started_at else None,
                semaphore_wait_ms=((started_at or finished_at) - queued_at) * 1000,
                result_snapshot=snapshot(observation, content),
            )
            name = identity["tool_name"]
            if result is not None and domain == "ok":
                if name == "search_nodes":
                    observe(observation, "search_returned", **identity,
                            content_snapshot=snapshot(observation, content))
                elif name == "read_node":
                    try:
                        data = json.loads(content) if isinstance(content, str) else content
                        if isinstance(data, dict) and isinstance(data.get("text"), str):
                            grounding_read(
                                observation, "tool_read_returned",
                                str((data.get("cite") or {}).get("handle") or call["args"].get(
                                    "node_id", ""
                                )), data["text"], repair_pass=identity["repair_pass"],
                                round_index=identity["round_index"],
                                tool_call_id=identity["tool_call_id"],
                            )
                    except Exception:
                        # Keep malformed optional provenance outside tool semantics.
                        observe(observation, "grounding_read_failed", **identity)
        except Exception:
            invalidate(observation)

    async def _run_tools(
        self, calls: list[dict], config: RunnableConfig | None,
        observation: TurnObservation | None = None, repair_pass: int = 0, round_index: int = 0,
    ) -> list[ToolMessage]:
        configured = int((config or {}).get("max_concurrency") or self._max_tool_concurrency)
        semaphore = asyncio.Semaphore(max(1, configured))
        return list(
            await asyncio.gather(
                *(self._run_tool(call, config, semaphore, observation, repair_pass, round_index)
                  for call in calls)
            )
        )

    @staticmethod
    def _observe_model_finish(
        observation: TurnObservation, reply: Any, aggregate: Any, *,
        repair_pass: int, round_index: int, model_call_id: str, status: str, started_at: float,
    ) -> None:
        try:
            observe(
                observation, "model_finished", repair_pass=repair_pass,
                round_index=round_index, model_call_id=model_call_id, status=status,
                invoke_wall_ms=(perf_counter() - started_at) * 1000,
                output_snapshot=snapshot(
                    observation, reply.model_dump(mode="json") if reply is not None else None
                ),
                usage=getattr(aggregate, "usage_metadata", None), physical_attempts=None,
            )
        except Exception:
            invalidate(observation)

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
        observation: TurnObservation | None = None,
        repair_pass: int = 0,
        **_: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        messages = self._messages(state)
        if observation is not None:
            try:
                observe(
                    observation, "runtime_config", repair_pass=repair_pass,
                    max_agent_rounds=self._max_agent_rounds,
                    max_tool_concurrency=int((config or {}).get("max_concurrency")
                                             or self._max_tool_concurrency),
                    model_route=getattr(self._model, "model_name", None),
                    temperature=getattr(self._model, "temperature", None),
                    configured_max_retries=getattr(self._model, "max_retries", None),
                    configured_max_output_tokens=getattr(self._model, "max_tokens", None),
                    configured_timeout_seconds=getattr(self._model, "request_timeout", None),
                    physical_attempts=None,
                    prompt_snapshot=snapshot(observation, self._system_prompt),
                    tool_schema_snapshot=snapshot(observation, [
                        convert_to_openai_tool(tool)
                        for tool in self._tools.values()
                    ]),
                )
            except Exception:
                invalidate(observation)
        for round_index in range(1, self._max_agent_rounds + 1):
            aggregate = None
            model_call_id = f"p{repair_pass}:r{round_index}"
            model_input(observation, messages, repair_pass=repair_pass, round_index=round_index,
                        model_call_id=model_call_id)
            started_at = perf_counter()
            status = "error"
            reply = None
            try:
                async with aclosing(self._bound_model.astream(messages, config=config)) as stream:
                    async for chunk in stream:
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        text = getattr(chunk, "content", None)
                        if isinstance(text, str) and text:
                            yield assistant_chunk(round_index, text)
                if aggregate is None:
                    raise RuntimeError("chat model returned an empty stream")
                reply = message_chunk_to_message(aggregate)
                reply, calls = self._prepare_reply(reply)
                status = "ok"
            except (asyncio.CancelledError, GeneratorExit):
                status = "cancelled"
                raise
            finally:
                if observation is not None:
                    self._observe_model_finish(
                        observation, reply, aggregate, repair_pass=repair_pass,
                        round_index=round_index, model_call_id=model_call_id,
                        status=status, started_at=started_at,
                    )
            if reply is None:
                raise RuntimeError("chat model returned no reply")
            messages.append(reply)
            for call in calls:
                raw_args = call.get("args")
                args = raw_args if isinstance(raw_args, dict) else {}
                observe(observation, "tool_proposed", repair_pass=repair_pass,
                        round_index=round_index, tool_call_id=str(call["id"]),
                        tool_name=str(call.get("name") or ""),
                        args_snapshot=snapshot(observation, args))
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
                for call in calls:
                    observe(observation, "tool_rejected", repair_pass=repair_pass,
                            round_index=round_index, tool_call_id=str(call["id"]),
                            reason="step_limit")
                raise AgentStepLimitError("native agent reached chat_max_agent_rounds")

            results = await self._run_tools(calls, config, observation, repair_pass, round_index)
            messages.extend(results)
            for call, result in zip(calls, results, strict=True):
                observe(observation, "tool_result_emitted", repair_pass=repair_pass,
                        round_index=round_index, tool_call_id=str(call["id"]),
                        tool_name=str(call.get("name") or ""))
                yield tool_result(
                    round_index,
                    str(call.get("id") or result.tool_call_id),
                    str(call.get("name") or result.name or ""),
                    result.content,
                    is_error=result.status == "error",
                )
        raise AgentStepLimitError("native agent reached chat_max_agent_rounds")

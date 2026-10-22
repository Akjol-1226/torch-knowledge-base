"""共享的一轮聊天执行流程，供 HTTP 路由和 A/B 评测复用。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing

from langchain_core.runnables import RunnableConfig
from starlette.concurrency import run_in_threadpool

from app.core.logging import get_logger
from app.modules.chat.observation import TurnObservation, finish, observe, snapshot
from app.modules.chat.sse import autofix_grounding, build_grounding_correction, events_from_runtime

log = get_logger("chat.execution")


async def execute_turn(
    runtime,
    messages: list,
    config: RunnableConfig,
    *,
    turn: str = "",
    observation: TurnObservation | None = None,
) -> AsyncIterator[dict]:
    """Shared streaming path, with optional private boundary observations.

    The terminal is runtime execution only; HTTP persistence is outside it.
    Closing this generator also closes the model/runtime generators.
    """
    observe(observation, "turn_started")
    terminal = False
    try:
        async with aclosing(_execute_turn(runtime, messages, config, turn, observation)) as stream:
            async for event in stream:
                if event.get("type") == "answer":
                    observe(observation, "turn_final_answer",
                            answer_snapshot=snapshot(observation, event))
                    observe(observation, "final_cited", handles=sorted(set(re.findall(
                        r"\[\[cite:([^\]]+)\]\]", event.get("text") or ""
                    ))))
                    finish(observation, "completed")
                    terminal = True
                yield event
    except (asyncio.CancelledError, GeneratorExit):
        if not terminal:
            finish(observation, "cancelled")
            terminal = True
        raise
    except Exception:
        finish(observation, "error")
        terminal = True
        raise
    finally:
        if not terminal:
            finish(observation, "cancelled")


async def _execute_turn(runtime, messages, config, turn, observation) -> AsyncGenerator[dict, None]:
    """执行正常轮和最多一次引用纠正轮，最终只下发一个 answer 事件。"""
    seed_cites = None
    final_event = None
    for attempt in range(2):
        pending = None
        kwargs = {"observation": observation, "repair_pass": attempt} if observation else {}
        async with aclosing(runtime.astream(
            {"messages": messages}, config=config, **kwargs
        )) as stream:
            async with aclosing(events_from_runtime(stream, seed_cites=seed_cites)) as mapped:
                async for event in mapped:
                    event_type = event.get("type")
                    if event_type == "answer":
                        pending = event
                    elif event_type == "chunk":
                        if attempt == 0:
                            yield event
                    else:
                        yield event

        ungrounded = (pending or {}).get("ungrounded") or []
        if attempt == 0 and ungrounded:
            if pending is None:
                break
            observe(observation, "grounding_repair_started", repair_pass=0)
            kwargs = {"observation": observation} if observation is not None else {}
            try:
                fixed = await run_in_threadpool(autofix_grounding, pending, ungrounded, **kwargs)
            finally:
                observe(observation, "grounding_repair_finished", repair_pass=0)
            if fixed is not None:
                log.info(
                    "chat_grounding_autofixed",
                    turn=turn,
                    n=len(ungrounded),
                    ids=ungrounded[:10],
                    missing=fixed.get("grounding_missing", []),
                )
                final_event = fixed
                break
            log.info("chat_grounding_correct", turn=turn, n=len(ungrounded), ids=ungrounded[:10])
            yield {
                "type": "tool",
                "name": f"核对引用·补读 {len(ungrounded)} 个未读但被引用的节点",
                "origin": "grounding_repair",
                "read_count": len(ungrounded),
            }
            observe(observation, "grounding_repair_started", repair_pass=1)
            try:
                correction_seed, corrective = await run_in_threadpool(
                    build_grounding_correction, ungrounded, **kwargs
                )
            finally:
                observe(observation, "grounding_repair_finished", repair_pass=1)
            seed_cites = ((pending or {}).get("read_cites") or []) + correction_seed
            messages = messages + [
                {"role": "assistant", "content": (pending or {}).get("text", "")},
                {"role": "user", "content": corrective},
            ]
            continue
        final_event = pending
        break

    yield final_event or {"type": "answer", "text": "", "sources": []}

"""共享的一轮聊天执行流程，供 HTTP 路由和 A/B 评测复用。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from starlette.concurrency import run_in_threadpool

from app.core.logging import get_logger
from app.modules.chat.sse import autofix_grounding, build_grounding_correction, events_from_runtime

log = get_logger("chat.execution")


async def execute_turn(
    runtime,
    messages: list,
    config: RunnableConfig,
    *,
    turn: str = "",
) -> AsyncIterator[dict]:
    """执行正常轮和最多一次引用纠正轮，最终只下发一个 answer 事件。"""
    seed_cites = None
    final_event = None
    for attempt in range(2):
        stream = runtime.astream({"messages": messages}, config=config)
        pending = None
        async for event in events_from_runtime(stream, seed_cites=seed_cites):
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
            fixed = await run_in_threadpool(autofix_grounding, pending, ungrounded)
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
            correction_seed, corrective = await run_in_threadpool(
                build_grounding_correction, ungrounded
            )
            seed_cites = ((pending or {}).get("read_cites") or []) + correction_seed
            messages = messages + [
                {"role": "assistant", "content": (pending or {}).get("text", "")},
                {"role": "user", "content": corrective},
            ]
            continue
        final_event = pending
        break

    yield final_event or {"type": "answer", "text": "", "sources": []}

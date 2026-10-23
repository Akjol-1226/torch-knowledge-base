"""项目唯一对话运行时的装配入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig

from app.core.config import get_settings
from app.modules.chat.model import build_chat_model
from app.modules.chat.native_runtime import NativeReactRuntime
from app.modules.chat.prompt import SYSTEM_PROMPT
from app.modules.chat.runtime_events import RuntimeEvent
from app.modules.chat.tools import KB_TOOLS


class ChatRuntime(Protocol):
    backend: str

    async def ainvoke(
        self, state: dict, config: RunnableConfig | None = None, **kwargs: Any
    ) -> dict: ...

    def astream(
        self,
        state: dict,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]: ...


def build_chat_runtime() -> ChatRuntime:
    settings = get_settings()
    return NativeReactRuntime(
        build_chat_model(),
        KB_TOOLS,
        SYSTEM_PROMPT,
        max_agent_rounds=settings.chat_max_agent_rounds,
        max_tool_concurrency=settings.chat_tool_concurrency,
    )

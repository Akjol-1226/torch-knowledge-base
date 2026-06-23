"""对话 agent（搬自 pageindex-agent/kb_agent/agent/graph.py）。

langgraph create_react_agent + 5 个只读知识库工具，在 PageIndex 文档树上自主漫游。
对话 LLM 统一走团队 LiteLLM Proxy（OpenAI 兼容），模型名走 config.chat_model。
"""

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.core.config import get_settings
from app.modules.chat.prompt import SYSTEM_PROMPT
from app.modules.chat.tools import KB_TOOLS


def build_chat_model() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.litellm_api_key.get_secret_value(),  # type: ignore[arg-type]
        base_url=settings.litellm_base_url,
        temperature=0,
        timeout=60,  # 上游卡住时不要无限挂住一个 worker
        max_retries=2,
    )


def build_agent(checkpointer=None):
    """checkpointer=None → 无服务端记忆（Web 端靠前端传 history 维持多轮）。

    切忌"既传全量 history 又用 checkpointer"——会双重叠加上下文（见原 pageindex-agent 注释）。
    需要服务端会话记忆（如 CLI REPL）时才显式传入 MemorySaver 并配合稳定 thread_id。
    """
    model = build_chat_model()
    return create_react_agent(
        model=model,
        tools=KB_TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

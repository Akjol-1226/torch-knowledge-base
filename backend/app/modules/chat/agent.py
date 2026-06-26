"""对话 agent（搬自 pageindex-agent/kb_agent/agent/graph.py）。

langgraph create_react_agent + 5 个只读知识库工具，在 PageIndex 文档树上自主漫游。
对话 LLM 统一走团队 LiteLLM Proxy（OpenAI 兼容），模型名走 config.chat_model。
"""

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.core.config import get_settings
from app.modules.chat.prompt import SYSTEM_PROMPT
from app.modules.chat.tools import KB_TOOLS


def build_chat_model() -> ChatOpenAI:
    settings = get_settings()
    # 全局限速（进程内、跨并发共享）：chat_rate_limit_rps>0 才启用，0=不限速。
    # agent 是懒单例 → 此 limiter 也是单例，所有对话请求共用同一令牌桶。
    rate_limiter = None
    if settings.chat_rate_limit_rps > 0:
        rate_limiter = InMemoryRateLimiter(
            requests_per_second=settings.chat_rate_limit_rps,
            check_every_n_seconds=0.1,
            max_bucket_size=max(1, int(settings.chat_rate_limit_rps)),
        )
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.litellm_api_key.get_secret_value(),  # type: ignore[arg-type]
        base_url=settings.litellm_base_url,
        temperature=0,
        timeout=60,  # 上游卡住时不要无限挂住一个 worker
        max_retries=2,
        rate_limiter=rate_limiter,
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

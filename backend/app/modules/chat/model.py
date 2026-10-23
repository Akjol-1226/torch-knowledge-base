"""对话模型构造器。"""

from typing import Any

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_openai import ChatOpenAI

from app.core.config import get_settings

CHAT_MODEL_TEMPERATURE = 0
CHAT_MODEL_TIMEOUT_SECONDS = 60
CHAT_MODEL_MAX_RETRIES = 2


def build_chat_model(
    *, timeout_seconds: float = CHAT_MODEL_TIMEOUT_SECONDS,
    max_retries: int = CHAT_MODEL_MAX_RETRIES,
    max_output_tokens: int | None = None,
) -> ChatOpenAI:
    settings = get_settings()
    rate_limiter = None
    if settings.chat_rate_limit_rps > 0:
        rate_limiter = InMemoryRateLimiter(
            requests_per_second=settings.chat_rate_limit_rps,
            check_every_n_seconds=0.1,
            max_bucket_size=max(1, int(settings.chat_rate_limit_rps)),
        )
    budget: dict[str, Any] = (
        {"max_tokens": max_output_tokens} if max_output_tokens is not None else {}
    )
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.litellm_api_key.get_secret_value(),  # type: ignore[arg-type]
        base_url=settings.litellm_base_url,
        temperature=CHAT_MODEL_TEMPERATURE,
        timeout=timeout_seconds,
        max_retries=max_retries,
        rate_limiter=rate_limiter,
        **budget,
    )

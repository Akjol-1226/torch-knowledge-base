from __future__ import annotations

import base64
import logging
import threading
import time
from pathlib import Path

from openai import OpenAI

from .config import Config, get_config

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_client_lock = threading.Lock()


def _get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            config = get_config()
            _client = OpenAI(api_key=config.api_key, base_url=config.api_base)
    return _client


def _generation_kwargs(config: Config) -> dict:
    kwargs: dict = {
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
    }
    if config.seed is not None:
        kwargs["seed"] = config.seed
    return kwargs


def _encode_image(image_path: Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_llm(
    system_prompt: str,
    user_text: str,
    model: str | None = None,
    max_retries: int = 3,
    timeout: float = 120.0,
    max_tokens: int | None = None,
) -> str:
    """Call Qwen LLM with text-only prompt (no image). Returns model text output."""
    config = get_config()
    client = _get_client()
    model = model or config.model

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    request_kwargs = _generation_kwargs(config)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    if not config.enable_thinking:
        request_kwargs["extra_body"] = {"enable_thinking": False}

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=timeout,
                **request_kwargs,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(
                "LLM text call failed (attempt %d/%d): %s — retrying in %ds",
                attempt + 1, max_retries, exc, wait,
            )
            time.sleep(wait)

    return ""  # unreachable


def call_vlm(
    image_path: Path,
    system_prompt: str,
    user_text: str,
    model: str | None = None,
    max_retries: int = 3,
    timeout: float = 120.0,
) -> str:
    """Call Qwen VLM with an image + text prompt. Returns model text output."""
    config = get_config()
    client = _get_client()
    model = model or config.model

    image_b64 = _encode_image(image_path)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": user_text},
            ],
        },
    ]

    request_kwargs = _generation_kwargs(config)
    if not config.enable_thinking:
        request_kwargs["extra_body"] = {"enable_thinking": False}

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=timeout,
                **request_kwargs,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(
                "VLM call failed (attempt %d/%d): %s — retrying in %ds",
                attempt + 1, max_retries, exc, wait,
            )
            time.sleep(wait)

    return ""  # unreachable; satisfies type checker

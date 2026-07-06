from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_agent: DoceditAgent | None = None


class DoceditAgentError(RuntimeError):
    pass


class AiEditResult(BaseModel):
    after_text: str
    explanation: str = ""
    changed: bool = True


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise DoceditAgentError("LLM 未返回 JSON 对象")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DoceditAgentError(f"LLM JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise DoceditAgentError("LLM JSON 顶层必须是对象")
    return payload


def parse_ai_response(text: str) -> AiEditResult:
    try:
        return AiEditResult.model_validate(_extract_json_object(text))
    except ValidationError as exc:
        raise DoceditAgentError(f"AI 编辑 JSON 结构不合法: {exc}") from exc


def _build_llm() -> ChatOpenAI:
    settings = get_settings()
    settings.apply_litellm_env()
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.litellm_api_key.get_secret_value(),  # type: ignore[arg-type]
        base_url=settings.litellm_base_url,
        temperature=0,
        timeout=120,
        max_retries=2,
    )


class DoceditAgent:
    def __init__(self, llm: ChatOpenAI | None = None):
        self.llm = llm or _build_llm()

    def edit(
        self,
        *,
        text: str,
        instruction: str,
        document: dict | None = None,
        node: dict | None = None,
    ) -> AiEditResult:
        prompt = (_PROMPT_DIR / "ai_edit.md").read_text(encoding="utf-8").format(
            document=json.dumps(document or {}, ensure_ascii=False),
            node=json.dumps(node or {}, ensure_ascii=False),
            text=text,
            instruction=instruction,
        )
        try:
            result = self.llm.invoke(prompt)
        except Exception as exc:
            raise DoceditAgentError(f"LLM 调用失败: {exc}") from exc
        content = getattr(result, "content", result)
        return parse_ai_response(str(content))


def get_agent() -> DoceditAgent:
    global _agent
    if _agent is None:
        _agent = DoceditAgent()
    return _agent


def set_agent_for_tests(agent: DoceditAgent | None) -> None:
    global _agent
    _agent = agent

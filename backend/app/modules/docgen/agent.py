from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.core.config import get_settings
from app.modules.docgen.schemas import (
    BlueprintItem,
    BlueprintSummaryLLMResponse,
    ChangePlanItem,
    ChangePlanLLMResponse,
    DocgenSessionRecord,
    OutlineSummaryItem,
)

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_agent: DocgenAgent | None = None


class DocgenAgentError(RuntimeError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise DocgenAgentError("LLM 未返回 JSON 对象")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DocgenAgentError(f"LLM JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise DocgenAgentError("LLM JSON 顶层必须是对象")
    return payload


def _read_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class DocgenAgent:
    def __init__(self, llm: ChatOpenAI | None = None):
        self.llm = llm or _build_llm()

    def _invoke_json(self, prompt: str) -> dict[str, Any]:
        try:
            result = self.llm.invoke(prompt)
        except Exception as exc:
            raise DocgenAgentError(f"LLM 调用失败: {exc}") from exc
        content = getattr(result, "content", result)
        return extract_json_object(str(content))

    def summarize_blueprint(
        self,
        blueprint: BlueprintItem,
        outline: list[OutlineSummaryItem],
    ) -> BlueprintSummaryLLMResponse:
        prompt = _read_prompt("blueprint_summary.md").format(
            blueprint=blueprint.model_dump_json(),
            outline=json.dumps([x.model_dump() for x in outline], ensure_ascii=False),
        )
        try:
            return BlueprintSummaryLLMResponse.model_validate(self._invoke_json(prompt))
        except ValidationError as exc:
            raise DocgenAgentError(f"蓝本摘要 JSON 结构不合法: {exc}") from exc

    def create_change_plan(
        self,
        *,
        session: DocgenSessionRecord,
        message: str,
        node_text_by_id: dict[str, str],
        current_plan: list[ChangePlanItem],
    ) -> ChangePlanLLMResponse:
        prompt = _read_prompt("change_plan.md").format(
            blueprint=session.blueprint.model_dump_json(),
            summary=session.summary,
            message=message,
            node_text_by_id=json.dumps(node_text_by_id, ensure_ascii=False),
            current_plan=json.dumps(
                [x.model_dump(mode="json") for x in current_plan],
                ensure_ascii=False,
            ),
        )
        try:
            return ChangePlanLLMResponse.model_validate(self._invoke_json(prompt))
        except ValidationError as exc:
            raise DocgenAgentError(f"改动清单 JSON 结构不合法: {exc}") from exc


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


def get_agent() -> DocgenAgent:
    global _agent
    if _agent is None:
        _agent = DocgenAgent()
    return _agent


def set_agent_for_tests(agent: DocgenAgent | None) -> None:
    global _agent
    _agent = agent

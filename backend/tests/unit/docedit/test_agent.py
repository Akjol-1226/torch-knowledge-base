from __future__ import annotations

import pytest

from app.modules.docedit.agent import (
    DoceditAgentError,
    _extract_json_object,
    get_agent,
    parse_ai_response,
)


def test_extract_json_object_from_fenced_json() -> None:
    raw = """```json
{"after_text":"new","explanation":"ok"}
```"""

    assert _extract_json_object(raw) == {"after_text": "new", "explanation": "ok"}


def test_parse_ai_response_rejects_missing_after_text() -> None:
    with pytest.raises(DoceditAgentError):
        parse_ai_response('{"explanation":"missing"}')


def test_agent_uses_chat_model_from_settings_without_real_llm(monkeypatch) -> None:
    monkeypatch.setenv("CHAT_MODEL", "test-chat-model")
    from app.core.config import get_settings

    get_settings.cache_clear()
    captured: dict[str, str] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["model"] = kwargs["model"]

    monkeypatch.setattr("app.modules.docedit.agent.ChatOpenAI", FakeChatOpenAI)

    get_agent()

    assert captured["model"] == "test-chat-model"

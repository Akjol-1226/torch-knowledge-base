from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessageChunk

from app.core.retrieval import TreeStore
from app.modules.chat import model as model_module
from app.modules.chat.execution import execute_turn
from app.modules.chat.native_runtime import NativeReactRuntime
from app.modules.chat.observation import TurnObservation
from app.modules.chat.tools import use_read_view
from evals.agent_trace import audit_trace


class Model:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    def bind_tools(self, tools):
        return self

    async def astream(self, messages, config=None):
        self.calls += 1
        self.inputs.append(list(messages))
        yield AIMessageChunk(content=(
            "First claim.[[cite:fiction:1]]" if self.calls == 1
            else "Corrected claim.[[cite:fiction:1]]"
        ))


class Store:
    def read_node(self, node_id):
        return {"text": "Frozen fictional source.", "title": "Fiction",
                "cite": {"handle": node_id, "doc_id": "fiction", "doc": "Fiction",
                         "section": "section", "lines": "1-2"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("policy, calls, final, stage", [
    ("autofix_then_rewrite", 1, "First claim.", "server_autofix_read"),
    ("rewrite_only", 2, "Corrected claim.", "correction_input_evidence"),
])
async def test_only_repair_policy_changes_server_read_vs_model_rewrite(policy, calls, final, stage):
    model = Model()
    runtime = NativeReactRuntime(model, [], "fixed system")
    recorder = TurnObservation("policy-run")
    with use_read_view(cast(TreeStore, Store())):
        events = [event async for event in execute_turn(
            runtime, [{"role": "user", "content": "Fixed question"}], {},
            observation=recorder, grounding_repair_policy=policy,
        )]
    assert model.calls == calls
    assert events[-1]["text"] == final + "[[cite:fiction:1]]"
    bundle = recorder.bundle()
    assert audit_trace(bundle)["execution_status"] == "completed"
    assert bundle["events"][0]["data"]["grounding_repair_policy"] == policy
    assert any(event["kind"] == stage for event in bundle["events"])
    if policy == "rewrite_only":
        assert "Frozen fictional source." in model.inputs[-1][-1].content
        assert not any(event["kind"] == "server_autofix_read" for event in bundle["events"])


@pytest.mark.asyncio
async def test_invalid_policy_is_rejected_before_any_model_call():
    model = Model()
    with pytest.raises(ValueError, match="policy"):
        _ = [event async for event in execute_turn(
            NativeReactRuntime(model, [], "fixed"), [], {},
            grounding_repair_policy=cast(Any, "invalid"),
        )]
    assert model.calls == 0


def test_explicit_model_budget_reaches_constructor_before_client_creation(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    settings = SimpleNamespace(
        chat_rate_limit_rps=0, chat_model="fictional", litellm_base_url="https://example.invalid",
        litellm_api_key=SimpleNamespace(get_secret_value=lambda: "fictional"),
    )
    monkeypatch.setattr(model_module, "get_settings", lambda: settings)
    monkeypatch.setattr(model_module, "ChatOpenAI", FakeChatOpenAI)
    model_module.build_chat_model(timeout_seconds=12, max_retries=0, max_output_tokens=800)
    assert captured["timeout"] == 12
    assert captured["max_retries"] == 0
    assert captured["max_tokens"] == 800

from types import SimpleNamespace

from pydantic import SecretStr

from app.modules.chat import model as model_mod
from app.modules.chat import runtime as runtime_mod
from app.modules.chat.native_runtime import NativeReactRuntime


class FakeModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self


def test_build_chat_runtime_uses_native_harness_configuration(monkeypatch):
    settings = SimpleNamespace(chat_max_agent_rounds=17, chat_tool_concurrency=3)
    model = FakeModel()
    monkeypatch.setattr(runtime_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime_mod, "build_chat_model", lambda: model)

    selected = runtime_mod.build_chat_runtime()

    assert isinstance(selected, NativeReactRuntime)
    assert selected.backend == "native"
    assert selected._max_agent_rounds == 17
    assert selected._max_tool_concurrency == 3
    assert model.tools == runtime_mod.KB_TOOLS
    assert selected._system_prompt == runtime_mod.SYSTEM_PROMPT


def test_chat_model_uses_manifested_deterministic_generation_config(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    settings = SimpleNamespace(
        chat_rate_limit_rps=0,
        chat_model="test-chat-model",
        litellm_api_key=SecretStr("test-key"),
        litellm_base_url="https://example.invalid/v1",
    )
    monkeypatch.setattr(model_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(model_mod, "ChatOpenAI", FakeChatOpenAI)

    model_mod.build_chat_model()

    assert model_mod.CHAT_MODEL_TEMPERATURE == 0
    assert captured["temperature"] == model_mod.CHAT_MODEL_TEMPERATURE
    assert captured["timeout"] == model_mod.CHAT_MODEL_TIMEOUT_SECONDS
    assert captured["max_retries"] == model_mod.CHAT_MODEL_MAX_RETRIES

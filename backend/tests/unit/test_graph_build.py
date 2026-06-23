from langchain_core.language_models.fake_chat_models import FakeChatModel

from app.modules.chat import agent as graph


class _ToolableFake(FakeChatModel):
    """FakeChatModel 子类：覆盖 bind_tools，使其在无真实 key 时也能通过 create_react_agent 组装。"""
    def bind_tools(self, tools, **kwargs):
        return self


def test_build_agent_has_tools_and_compiles(monkeypatch):
    # 避免真实创建 ChatOpenAI 需要 key：替身一个可绑定工具的占位 model
    fake = _ToolableFake(responses=["ok"])
    monkeypatch.setattr(graph, "build_chat_model", lambda: fake)
    agent = graph.build_agent()
    # 编译出的 graph 可调用 .invoke / .stream（属性存在即可）
    assert hasattr(agent, "invoke") and hasattr(agent, "stream")

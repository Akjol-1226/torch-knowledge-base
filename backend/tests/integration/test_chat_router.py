"""chat 路由集成测试（搬自 pageindex-agent/tests/test_app.py，适配我们的 main.app）。

用 FakeAgent monkeypatch 掉 router.get_agent，不触碰真实 LLM；验证 SSE 事件契约、
错误事件不伪装成 answer、history 拼接。
"""

import json

from fastapi.testclient import TestClient

from app.main import app
from app.modules.chat import router as router_mod


class FakeAgent:
    async def ainvoke(self, inp, config=None, **kwargs):
        class M:  # 末条消息
            content = "答案。来源：D · S · 行1-2"

        return {"messages": [M()]}

    async def astream(self, inp, stream_mode=None, config=None, **kwargs):
        tool_msg = type(
            "TM",
            (),
            {"content": json.dumps({"cite": {"doc": "D", "section": "S", "lines": "1-2"}}, ensure_ascii=False)},
        )()
        tool_msg.__class__.__name__ = "ToolMessage"
        ai = type(
            "AI",
            (),
            {"tool_calls": [{"name": "read_node", "args": {"node_id": "d:0"}}], "content": ""},
        )()
        chunk = type("CK", (), {"content": "答案。", "tool_calls": []})()
        yield ("updates", {"agent": {"messages": [ai]}})
        yield ("updates", {"tools": {"messages": [tool_msg]}})
        yield ("messages", (chunk, {"langgraph_node": "agent"}))


def _client(monkeypatch):
    monkeypatch.setattr(router_mod, "get_agent", lambda: FakeAgent())
    return TestClient(app)


def test_health(monkeypatch):
    assert _client(monkeypatch).get("/health").json() == {"status": "ok"}


def test_chat_returns_answer(monkeypatch):
    r = _client(monkeypatch).post("/chat", json={"message": "问题"})
    assert r.status_code == 200
    assert "答案" in r.json()["answer"]


def test_chat_stream_emits_events(monkeypatch):
    r = _client(monkeypatch).post("/chat/stream", json={"message": "问题"})
    assert r.status_code == 200
    body = r.text
    assert '"type": "tool"' in body
    assert '"type": "answer"' in body
    # sources 是按文档分组的结构化对象（cite 无 handle/doc_id 时兜底用 doc 名分组）
    assert '"doc_id": "D"' in body
    assert '"section": "S"' in body


class BoomAgent:
    async def astream(self, inp, stream_mode=None, config=None, **kwargs):
        raise RuntimeError("boom")
        yield  # 使其成为 async generator


def test_chat_stream_error_emits_error_not_answer(monkeypatch):
    monkeypatch.setattr(router_mod, "get_agent", lambda: BoomAgent())
    r = TestClient(app).post("/chat/stream", json={"message": "问题"})
    assert r.status_code == 200
    body = r.text
    assert '"type": "error"' in body  # 出错时发 error
    assert '"type": "answer"' not in body  # 绝不伪装成完成的回答


def test_build_messages_includes_history():
    from app.modules.chat.router import ChatRequest, _build_messages

    req = ChatRequest(
        message="那它呢",
        history=[
            {"role": "user", "content": "HJ900001 结论?"},
            {"role": "assistant", "content": "符合要求", "sources": ["x"]},
        ],
    )
    msgs = _build_messages(req)
    assert msgs[0] == {"role": "user", "content": "HJ900001 结论?"}
    assert msgs[1] == {"role": "assistant", "content": "符合要求"}  # sources 被剥离
    assert msgs[-1] == {"role": "user", "content": "那它呢"}

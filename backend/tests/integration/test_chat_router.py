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
    assert '"type": "source"' not in body
    assert '"type": "answer"' in body
    # sources 是按文档分组的结构化对象（cite 无 handle/doc_id 时兜底用 doc 名分组）
    assert '"doc_id": "D"' in body
    assert '"section": "S"' in body


def test_chat_stream_autofixes_small_ungrounded_without_second_agent_turn(monkeypatch):
    class UngroundedAgent:
        def __init__(self):
            self.calls = 0

        async def astream(self, inp, stream_mode=None, config=None, **kwargs):
            self.calls += 1
            chunk = type("CK", (), {"content": "结论[[cite:doc_x:0001]]", "tool_calls": []})()
            yield ("messages", (chunk, {"langgraph_node": "agent"}))

    class FakeStore:
        def read_node(self, hid):
            return {
                "title": "流程图",
                "text": "节点列表：J01 进料检验判定……",
                "cite": {
                    "doc": "工艺",
                    "section": "流程图",
                    "lines": "1-9",
                    "handle": hid,
                    "doc_id": "doc_x",
                },
            }

    import app.modules.chat.tools as tools_mod

    agent = UngroundedAgent()
    monkeypatch.setattr(router_mod, "get_agent", lambda: agent)
    monkeypatch.setattr(tools_mod, "get_store", lambda: FakeStore())

    r = TestClient(app).post("/chat/stream", json={"message": "问题"})

    assert r.status_code == 200
    body = r.text
    assert agent.calls == 1
    assert "核对引用" not in body
    assert '"handle": "doc_x:0001"' in body


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


def test_node_context_skips_ocr_rects_when_disabled(monkeypatch, tmp_path):
    class FakeStore:
        def read_node(self, handle):
            return {
                "title": "涂布",
                "text": "涂布正文",
                "prev_id": None,
                "next_id": None,
                "section": {},
                "cite": {
                    "doc_id": "doc_demo",
                    "doc": "demo",
                    "section": "涂布",
                    "lines": "1-2",
                    "page": 3,
                },
                "path": "demo > 涂布",
            }

        def open_node(self, handle):
            return {"children": []}

    md_path = tmp_path / "demo.md"
    md_path.write_text("# 涂布\n正文", encoding="utf-8")
    monkeypatch.setattr(router_mod, "get_store", lambda: FakeStore())

    from app.modules.ingest import document_service, ocr_locate

    monkeypatch.setattr(document_service, "get_md_path", lambda doc_id: md_path)

    def fail_load_ocr(*args, **kwargs):
        raise AssertionError("OCR should be disabled by default")

    monkeypatch.setattr(ocr_locate, "load_ocr", fail_load_ocr)

    r = TestClient(app).get("/chat/node", params={"handle": "doc_demo:0001"})

    assert r.status_code == 200
    assert r.json()["rects"] == []

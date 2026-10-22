"""chat 路由集成测试（搬自 pageindex-agent/tests/test_app.py，适配我们的 main.app）。

用 FakeAgent monkeypatch 掉 router.get_agent，不触碰真实 LLM；验证 SSE 事件契约、
错误事件不伪装成 answer、history 拼接。
"""

import json

from fastapi.testclient import TestClient

from app.main import app
from app.modules.chat import router as router_mod


class FakeAgent:
    backend = "native"

    async def ainvoke(self, inp, config=None, **kwargs):
        class M:  # 末条消息
            content = "答案。来源：D · S · 行1-2"

        return {"messages": [M()]}

    async def astream(self, inp, config=None, **kwargs):
        yield {
            "type": "tool_call",
            "round": 1,
            "call_id": "call-1",
            "name": "read_node",
            "args": {"node_id": "d:0"},
        }
        yield {"type": "assistant_complete", "round": 1, "has_tool_calls": True}
        yield {
            "type": "tool_result",
            "round": 1,
            "call_id": "call-1",
            "name": "read_node",
            "content": {"cite": {"doc": "D", "section": "S", "lines": "1-2"}},
            "is_error": False,
        }
        yield {"type": "assistant_chunk", "round": 2, "text": "答案。"}
        yield {"type": "assistant_complete", "round": 2, "has_tool_calls": False}


def _client(monkeypatch):
    monkeypatch.setattr(router_mod, "get_agent", lambda: FakeAgent())
    return TestClient(app)


def _sse_events(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_health(monkeypatch):
    assert _client(monkeypatch).get("/health").json() == {"status": "ok"}


def test_chat_returns_answer(monkeypatch):
    r = _client(monkeypatch).post("/chat", json={"message": "问题"})
    assert r.status_code == 200
    assert "答案" in r.json()["answer"]


def test_chat_stream_emits_events(monkeypatch):
    r = _client(monkeypatch).post("/chat/stream", json={"message": "问题"})
    assert r.status_code == 200
    events = _sse_events(r)
    assert [event["type"] for event in events] == [
        "tool",
        "tool_result",
        "chunk",
        "answer",
    ]
    assert sum(event["type"] == "answer" for event in events) == 1
    assert not [event for event in events if event["type"] == "source"]
    # sources 是按文档分组的结构化对象（cite 无 handle/doc_id 时兜底用 doc 名分组）
    assert events[-1]["sources"][0]["doc_id"] == "D"
    assert events[-1]["sources"][0]["nodes"][0]["section"] == "S"


def test_chat_stream_persists_successful_turn(monkeypatch):
    calls = []

    def append_turn(*args):
        calls.append(args)

    monkeypatch.setattr(router_mod, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(router_mod.conversation_service, "append_turn", append_turn)

    response = TestClient(app).post(
        "/chat/stream",
        json={"message": "问题", "conversation_id": "conv-1"},
    )
    events = _sse_events(response)

    assert calls and calls[0][0] == "conv-1"
    assert calls[0][2] == events[-1]["text"] == "答案。"


def test_chat_stream_does_not_persist_tool_round_preamble(monkeypatch):
    class PreambleRuntime(FakeAgent):
        async def astream(self, inp, config=None, **kwargs):
            yield {"type": "assistant_chunk", "round": 1, "text": "我先查看原文。"}
            yield {"type": "assistant_complete", "round": 1, "has_tool_calls": True}
            yield {"type": "assistant_chunk", "round": 2, "text": "最终结论。"}
            yield {"type": "assistant_complete", "round": 2, "has_tool_calls": False}

    stored = []
    monkeypatch.setattr(router_mod, "get_agent", lambda: PreambleRuntime())
    monkeypatch.setattr(
        router_mod.conversation_service, "append_turn", lambda *args: stored.append(args)
    )

    response = TestClient(app).post(
        "/chat/stream", json={"message": "问题", "conversation_id": "conv-preamble"}
    )
    events = _sse_events(response)

    assert [event["text"] for event in events if event["type"] == "chunk"] == [
        "我先查看原文。", "最终结论。"
    ]
    assert events[-1]["text"] == stored[0][2] == "最终结论。"
    assert sum(event["type"] == "answer" for event in events) == 1


def test_sse_keeps_runtime_counters_internal_without_mutating_the_event():
    event = {"type": "tool", "name": "核对引用", "origin": "grounding_repair", "read_count": 2}
    frame = router_mod._sse(event)

    assert json.loads(frame.removeprefix("data: ")) == {"type": "tool", "name": "核对引用"}
    assert event["read_count"] == 2


def test_chat_request_keeps_legacy_thread_id():
    request = router_mod.ChatRequest(message="问题", thread_id="legacy-thread")
    assert request.thread_id == "legacy-thread"


def test_chat_stream_autofixes_small_ungrounded_without_second_agent_turn(monkeypatch):
    class UngroundedAgent:
        backend = "native"

        def __init__(self):
            self.calls = 0

        async def astream(self, inp, config=None, **kwargs):
            self.calls += 1
            yield {
                "type": "assistant_chunk",
                "round": 1,
                "text": "结论[[cite:doc_x:0001]]",
            }
            yield {"type": "assistant_complete", "round": 1, "has_tool_calls": False}

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
    assert "grounding_read_count" not in body


class BoomAgent:
    backend = "native"

    async def astream(self, inp, config=None, **kwargs):
        raise RuntimeError("boom")
        yield  # 使其成为 async generator


def test_chat_stream_error_emits_error_not_answer(monkeypatch):
    monkeypatch.setattr(router_mod, "get_agent", lambda: BoomAgent())
    r = TestClient(app).post("/chat/stream", json={"message": "问题"})
    assert r.status_code == 200
    body = r.text
    assert '"type": "error"' in body  # 出错时发 error
    assert '"type": "answer"' not in body  # 绝不伪装成完成的回答


def test_chat_stream_partial_chunk_then_error_has_no_answer(monkeypatch):
    class PartialBoomAgent:
        backend = "native"

        async def astream(self, inp, config=None, **kwargs):
            yield {"type": "assistant_chunk", "round": 1, "text": "部分内容"}
            raise RuntimeError("boom after chunk")

    monkeypatch.setattr(router_mod, "get_agent", lambda: PartialBoomAgent())

    response = TestClient(app).post("/chat/stream", json={"message": "问题"})
    events = _sse_events(response)

    assert [event["type"] for event in events] == ["chunk", "error"]
    assert not [event for event in events if event["type"] == "answer"]


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

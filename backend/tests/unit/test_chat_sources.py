import types

from fastapi.testclient import TestClient

from app.main import app
from app.modules.chat import conversation_service
from app.modules.chat import router as chat_router


class FakeStore:
    """最小 TreeStore 替身：一个被切成 2 段同名窗口的工序。"""

    _NODES = {
        "doc_a:0001": {
            "title": "G01 配料工序", "text": "本工序正文：移入、装钵。",
            "cite": {"doc": "工艺文件", "section": "G01 配料工序", "lines": "1-10",
                     "handle": "doc_a:0001", "doc_id": "doc_a", "page": 3},
            "prev_id": None, "next_id": "doc_a:0002",
            "section": {"part": 1, "total": 2, "span": ["doc_a:0001", "doc_a:0002"]},
        },
        "doc_a:0002": {
            "title": "G01 配料工序", "text": "续段：装炉、配料。",
            "cite": {"doc": "工艺文件", "section": "G01 配料工序", "lines": "11-20",
                     "handle": "doc_a:0002", "doc_id": "doc_a", "page": 4},
            "prev_id": "doc_a:0001", "next_id": None,
        },
    }

    def read_node(self, h):
        return self._NODES.get(h, {"error": f"unknown node: {h}"})


def test_node_context_returns_span_with_cited_flag(monkeypatch):
    monkeypatch.setattr(chat_router, "get_store", lambda: FakeStore())
    r = TestClient(app).get("/chat/node", params={"handle": "doc_a:0001"})
    assert r.status_code == 200
    data = r.json()
    assert data["doc_name"] == "工艺文件"
    assert data["doc_id"] == "doc_a"   # 前端拼 /ingest/document/{doc_id}/pdf 用
    assert data["page"] == 3           # 被引用节点所在 PDF 页（原文 PDF 跳页用）
    ctx = data["context"]
    assert [c["handle"] for c in ctx] == ["doc_a:0001", "doc_a:0002"]  # 取了整个 section.span
    assert [c["page"] for c in ctx] == [3, 4]                          # 每段各自带页码
    cited = [c for c in ctx if c["is_cited"]]
    assert len(cited) == 1 and cited[0]["handle"] == "doc_a:0001"      # 仅本节点标记为引用处
    assert "移入、装钵" in ctx[0]["text"]                                # 返回整段正文


def test_node_context_prev_next_branch(monkeypatch):
    # 无 section 的普通节点 → 取 prev/本/next 作为上下文，仅本节点标 is_cited
    monkeypatch.setattr(chat_router, "get_store", lambda: FakeStore())
    r = TestClient(app).get("/chat/node", params={"handle": "doc_a:0002"})
    assert r.status_code == 200
    ctx = r.json()["context"]
    assert [c["handle"] for c in ctx] == ["doc_a:0001", "doc_a:0002"]  # prev + 本（next 为 None 被过滤）
    cited = [c for c in ctx if c["is_cited"]]
    assert len(cited) == 1 and cited[0]["handle"] == "doc_a:0002"


def test_node_context_404(monkeypatch):
    monkeypatch.setattr(chat_router, "get_store", lambda: FakeStore())
    r = TestClient(app).get("/chat/node", params={"handle": "nope:9999"})
    assert r.status_code == 404


def test_append_turn_persists_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(
        conversation_service, "get_settings",
        lambda: types.SimpleNamespace(data_dir=tmp_path),
    )
    sources = [{
        "doc_id": "doc_a", "doc_name": "工艺文件",
        "nodes": [{"handle": "doc_a:0001", "section": "G01", "lines": "1-10", "snippet": "x"}],
    }]
    conversation_service.append_turn("conv1", "问题", "答案", sources)
    rec = conversation_service.get_conversation("conv1")
    assistant = [m for m in rec["messages"] if m["role"] == "assistant"][0]
    assert assistant["sources"] == sources  # 随会话持久化，重开历史可复原

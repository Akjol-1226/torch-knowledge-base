import json

from app.modules.chat.sse import events_from_stream, extract_cite


class FakeAI:
    """模拟带 tool_calls 的 AIMessage（updates 模式里出现）。"""
    def __init__(self, tool_calls=None, content=""):
        self.tool_calls = tool_calls or []
        self.content = content


class FakeChunk:
    """模拟 messages 模式的 AIMessageChunk。"""
    def __init__(self, content):
        self.content = content
        self.tool_calls = []


def _tool_msg(payload):
    """构造一个 content 为 JSON 的 ToolMessage。"""
    tm = type("TM", (), {"content": json.dumps(payload, ensure_ascii=False)})()
    tm.__class__.__name__ = "ToolMessage"
    return tm


def test_extract_cite_returns_structured_dict_from_read_node():
    payload = json.dumps({"cite": {"doc": "HJ900001鉴定报告", "section": "检验报告",
                                   "lines": "43-79", "handle": "doc_1:0005", "doc_id": "doc_1"}},
                         ensure_ascii=False)
    assert extract_cite(payload) == [{"doc": "HJ900001鉴定报告", "section": "检验报告",
                                      "lines": "43-79", "handle": "doc_1:0005", "doc_id": "doc_1"}]


def test_extract_cite_from_search_hits_list():
    payload = json.dumps([{"cite": {"doc": "D", "section": "S1", "lines": "1-2", "handle": "d:1", "doc_id": "d"}},
                          {"cite": {"doc": "D", "section": "S2", "lines": "3-4", "handle": "d:2", "doc_id": "d"}}],
                         ensure_ascii=False)
    assert [c["handle"] for c in extract_cite(payload)] == ["d:1", "d:2"]


def test_events_sequence_tool_chunk_answer():
    # 模拟：agent 节点先发一个 tool_call(updates) → tool 结果(updates) → 答案 token(messages)
    tool_ai = FakeAI(tool_calls=[{"name": "read_node", "args": {"node_id": "doc_a:0003"}}])
    tool_msg = _tool_msg({"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                                   "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}})
    stream = [
        ("updates", {"agent": {"messages": [tool_ai]}}),
        ("updates", {"tools": {"messages": [tool_msg]}}),
        ("messages", (FakeChunk("峰值"), {"langgraph_node": "agent"})),
        ("messages", (FakeChunk("245℃"), {"langgraph_node": "agent"})),
    ]
    evs = list(events_from_stream(stream))
    types = [e["type"] for e in evs]
    # tool_result：工具原始返回，供前端展开看 call/response（开发调试）
    assert types == ["tool", "tool_result", "chunk", "chunk", "answer"]
    assert evs[0] == {"type": "tool", "name": "read_node", "args": {"node_id": "doc_a:0003"}}
    assert evs[1]["type"] == "tool_result" and "工艺文件" in evs[1]["content"]
    assert evs[2]["text"] == "峰值" and evs[3]["text"] == "245℃"
    answer = evs[4]
    assert answer["type"] == "answer" and answer["text"] == "峰值245℃"
    # sources 是按文档分组的结构化对象（不再是拍平字符串）
    assert answer["sources"] == [
        {"doc_id": "doc_a", "doc_name": "工艺文件",
         "nodes": [{"handle": "doc_a:0003", "section": "5.3 回流焊", "lines": "8-14", "snippet": "峰值245℃"}]}
    ]


def test_sources_group_by_doc_and_dedup_node():
    """同一文档的多个 node 归到一张卡（诉求1）；重复 handle 去重；保留首次出现顺序。"""
    hits = [
        {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                  "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}},
        {"cite": {"doc": "工艺文件", "section": "6 检验", "lines": "15-20",
                  "handle": "doc_a:0007", "doc_id": "doc_a", "snippet": "外观"}},
        {"cite": {"doc": "鉴定报告", "section": "结论", "lines": "1-5",
                  "handle": "doc_b:0001", "doc_id": "doc_b", "snippet": "合格"}},
    ]
    dup = {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                    "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}}
    stream = [
        ("updates", {"tools": {"messages": [_tool_msg(hits)]}}),
        ("updates", {"tools": {"messages": [_tool_msg(dup)]}}),
    ]
    src = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]["sources"]
    # 文档级分组、保序：doc_a 在前，doc_b 在后
    assert [g["doc_id"] for g in src] == ["doc_a", "doc_b"]
    # doc_a 下 2 个 node（重复的 0003 去重后只剩 1 个）
    assert [n["handle"] for n in src[0]["nodes"]] == ["doc_a:0003", "doc_a:0007"]
    assert [n["handle"] for n in src[1]["nodes"]] == ["doc_b:0001"]
    # snippet 透传，供前端高亮
    assert src[0]["nodes"][0]["snippet"] == "峰值245℃"


def test_cite_without_handle_falls_back_gracefully():
    """cite 缺 handle/doc_id（旧数据/手构）时，用 doc+section+lines 兜底去重、用 doc 名分组。"""
    c1 = {"cite": {"doc": "D", "section": "S", "lines": "1-2"}}
    stream = [
        ("updates", {"tools": {"messages": [_tool_msg(c1)]}}),
        ("updates", {"tools": {"messages": [_tool_msg(c1)]}}),  # 完全相同 → 去重
    ]
    src = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]["sources"]
    assert len(src) == 1 and src[0]["doc_id"] == "D"
    assert len(src[0]["nodes"]) == 1

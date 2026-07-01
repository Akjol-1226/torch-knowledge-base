import json

from app.modules.chat.sse import autofix_grounding, events_from_stream, extract_cite


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


def test_read_node_does_not_emit_incremental_source_event_before_answer():
    """数据来源恢复为最终 answer 一起返回，不在流式中途单独发 source 事件。"""
    rn = {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                   "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}}
    stream = [
        ("updates", {"tools": {"messages": [_tool_msg(rn)]}}),
        ("messages", (FakeChunk("峰值 245℃[[cite:doc_a:0003]]"), {"langgraph_node": "agent"})),
    ]
    evs = list(events_from_stream(stream))
    types = [e["type"] for e in evs]

    assert types == ["tool_result", "chunk", "answer"]
    assert evs[-1]["sources"] == [
        {"doc_id": "doc_a", "doc_name": "工艺文件",
         "nodes": [{"handle": "doc_a:0003", "section": "5.3 回流焊", "lines": "8-14", "snippet": "峰值245℃"}]}
    ]


def test_sources_group_by_doc_and_dedup_node():
    """同一文档的多个 read_node 归到一张卡（诉求1）；重复 handle 去重；保留首次出现顺序。
    每次 read_node 返回一个含 cite 的 dict（只有 read_node 才进 sources）。"""
    n3 = {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                   "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}}
    n7 = {"cite": {"doc": "工艺文件", "section": "6 检验", "lines": "15-20",
                   "handle": "doc_a:0007", "doc_id": "doc_a", "snippet": "外观"}}
    nb = {"cite": {"doc": "鉴定报告", "section": "结论", "lines": "1-5",
                   "handle": "doc_b:0001", "doc_id": "doc_b", "snippet": "合格"}}
    stream = [
        ("updates", {"tools": {"messages": [_tool_msg(n3)]}}),
        ("updates", {"tools": {"messages": [_tool_msg(n7)]}}),
        ("updates", {"tools": {"messages": [_tool_msg(nb)]}}),
        ("updates", {"tools": {"messages": [_tool_msg(n3)]}}),  # 重复 handle → 去重
    ]
    src = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]["sources"]
    # 文档级分组、保序：doc_a 在前，doc_b 在后
    assert [g["doc_id"] for g in src] == ["doc_a", "doc_b"]
    # doc_a 下 2 个 node（重复的 0003 去重后只剩 1 个）
    assert [n["handle"] for n in src[0]["nodes"]] == ["doc_a:0003", "doc_a:0007"]
    assert [n["handle"] for n in src[1]["nodes"]] == ["doc_b:0001"]
    # snippet 透传，供前端高亮
    assert src[0]["nodes"][0]["snippet"] == "峰值245℃"


def test_search_results_do_not_enter_sources():
    """search_nodes 返回的命中列表只是定位线索，不读全文就不算数据来源——不进 sources，
    其 [[cite]] 也无法在前端成有效上标（确保回答基于 read_node 全文）。"""
    search_hits = [
        {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                  "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}},
        {"cite": {"doc": "工艺文件", "section": "6 检验", "lines": "15-20",
                  "handle": "doc_a:0007", "doc_id": "doc_a", "snippet": "外观"}},
    ]
    stream = [
        # 名为 search_nodes 的工具结果（list）→ 不进 sources
        ("updates", {"tools": {"messages": [_tool_msg(search_hits)]}}),
        ("messages", (FakeChunk("回流焊峰值 245℃[[cite:doc_a:0003]]"), {"langgraph_node": "agent"})),
    ]
    ans = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]
    assert ans["sources"] == []          # 搜到但没 read_node → 无数据来源
    assert "245℃" in ans["text"]         # 答案文本照常返回（前端会丢弃悬空上标）
    assert not [e for e in events_from_stream(stream) if e["type"] == "source"]


def test_read_then_cite_enters_sources():
    """先 read_node 读过，再引用同一 handle → 进 sources、视为有据引用。"""
    rn = {"cite": {"doc": "工艺文件", "section": "5.3 回流焊", "lines": "8-14",
                   "handle": "doc_a:0003", "doc_id": "doc_a", "snippet": "峰值245℃"}}
    stream = [
        ("updates", {"tools": {"messages": [_tool_msg(rn)]}}),
        ("messages", (FakeChunk("峰值 245℃[[cite:doc_a:0003]]"), {"langgraph_node": "agent"})),
    ]
    ans = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]
    assert [n["handle"] for d in ans["sources"] for n in d["nodes"]] == ["doc_a:0003"]


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


def test_answer_reports_ungrounded_citation():
    """正文引用了本轮没 read_node 读过的节点 → answer.ungrounded 列出它（供 router 触发补读纠正）。"""
    stream = [("messages", (FakeChunk("结论[[cite:doc_x:0009]]"), {"langgraph_node": "agent"}))]
    ans = [e for e in events_from_stream(stream) if e["type"] == "answer"][0]
    assert ans["ungrounded"] == ["doc_x:0009"]
    assert ans["sources"] == []


def test_seed_cites_marks_node_as_grounded():
    """注入 seed_cites（服务端已补读）后，同一引用不再算 ungrounded，且进 sources。"""
    seed = [{"doc": "工艺", "section": "S", "lines": "1-2", "handle": "doc_x:0009", "doc_id": "doc_x"}]
    stream = [("messages", (FakeChunk("结论[[cite:doc_x:0009]]"), {"langgraph_node": "agent"}))]
    ans = [e for e in events_from_stream(stream, seed_cites=seed) if e["type"] == "answer"][0]
    assert ans["ungrounded"] == []
    assert [n["handle"] for g in ans["sources"] for n in g["nodes"]] == ["doc_x:0009"]


def test_autofix_grounding_adds_readable_sources_and_drops_missing(monkeypatch):
    """少量未接地引用走轻量补读：读得到的加入 sources，读不到的引用标记直接删除。"""
    import app.modules.chat.tools as tools_mod

    class FakeStore:
        def read_node(self, hid):
            if hid == "doc_x:0001":
                return {"title": "流程图", "text": "节点列表：J01 进料检验判定……",
                        "cite": {"doc": "工艺", "section": "流程图", "lines": "1-9",
                                 "handle": "doc_x:0001", "doc_id": "doc_x"}}
            return {"error": "not found"}

    monkeypatch.setattr(tools_mod, "get_store", lambda: FakeStore())
    ev = {
        "type": "answer",
        "text": "已读结论[[cite:doc_x:0001]]，缺失结论[[cite:doc_x:9999]]",
        "sources": [],
        "ungrounded": ["doc_x:0001", "doc_x:9999"],
        "read_cites": [],
    }

    fixed = autofix_grounding(ev, ev["ungrounded"])

    assert fixed is not None
    assert fixed["ungrounded"] == []
    assert "[[cite:doc_x:0001]]" in fixed["text"]
    assert "[[cite:doc_x:9999]]" not in fixed["text"]
    assert [n["handle"] for g in fixed["sources"] for n in g["nodes"]] == ["doc_x:0001"]


def test_autofix_grounding_declines_many_ungrounded_ids():
    ev = {"type": "answer", "text": "", "sources": [], "ungrounded": [], "read_cites": []}
    assert autofix_grounding(ev, ["a:1", "a:2", "a:3", "a:4"]) is None


def test_build_grounding_correction(monkeypatch):
    """补读：能读到的 → seed_cite + 回灌正文；读不到的（凭空标的）→ 列入"必须删除"。"""
    import app.modules.chat.tools as tools_mod
    from app.modules.chat.sse import build_grounding_correction

    class FakeStore:
        def read_node(self, hid):
            if hid == "doc_x:0001":
                return {"title": "流程图", "text": "节点列表：J01 进料检验判定……",
                        "cite": {"doc": "工艺", "section": "流程图", "lines": "1-9",
                                 "handle": "doc_x:0001", "doc_id": "doc_x"}}
            return {"error": "not found"}

    monkeypatch.setattr(tools_mod, "get_store", lambda: FakeStore())
    seed, text = build_grounding_correction(["doc_x:0001", "doc_x:9999"])
    assert [c["handle"] for c in seed] == ["doc_x:0001"]   # 只有读到的进 seed
    assert "节点列表" in text                                # 能读到的回灌正文供核对
    assert "doc_x:9999" in text and "读不到" in text         # 读不到的列入删除清单

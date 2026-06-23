import json


def extract_cite(content) -> list:
    """从工具返回内容抽结构化 cite dict。content 可能是 dict、list 或 JSON 字符串。"""
    data = content
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except Exception:
            return []
    out = []
    if isinstance(data, dict) and isinstance(data.get("cite"), dict):
        out.append(data["cite"])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("cite"), dict):
                out.append(item["cite"])
    return out


def _new_state():
    # sources: 按文档分组的列表；doc_index: doc_id -> 该分组对象（便于追加 node）；
    # seen: 已收录 node 的去重键集合
    return {"final": [], "sources": [], "seen": set(), "doc_index": {}}


def _add_cite(state, cite: dict) -> None:
    """把一个 cite dict 按文档分组、node 按 handle 去重累积进 state['sources']。
    cite 缺 handle/doc_id 时（旧数据/手构）用 doc+section+lines 兜底去重、用 doc 名分组。"""
    handle = cite.get("handle") or "·".join(
        (cite.get("doc", ""), cite.get("section", ""), cite.get("lines", ""))
    )
    if handle in state["seen"]:
        return
    state["seen"].add(handle)
    doc_id = cite.get("doc_id") or cite.get("doc", "")
    grp = state["doc_index"].get(doc_id)
    if grp is None:
        grp = {"doc_id": doc_id, "doc_name": cite.get("doc", ""), "nodes": []}
        state["doc_index"][doc_id] = grp
        state["sources"].append(grp)
    grp["nodes"].append({
        "handle": cite.get("handle", ""),
        "section": cite.get("section", ""),
        "lines": cite.get("lines", ""),
        "snippet": cite.get("snippet", ""),
    })


def _map_event(mode, data, state):
    """把一个 (mode,data) 事件映射成 0..N 个前端事件，并累积最终答案/引用到 state。"""
    events = []
    if mode == "messages":
        chunk, meta = data
        text = getattr(chunk, "content", "") or ""
        node = (meta or {}).get("langgraph_node")
        # 只把 agent 节点产出的、有内容的 token 当答案流
        if text and node in (None, "agent"):
            state["final"].append(text)
            events.append({"type": "chunk", "text": text})
    elif mode == "updates":
        for _node, upd in (data or {}).items():
            for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                for tc in (getattr(m, "tool_calls", None) or []):
                    events.append(
                        {"type": "tool", "name": tc.get("name", ""), "args": tc.get("args", {})}
                    )
                if m.__class__.__name__ == "ToolMessage":
                    raw = getattr(m, "content", "")
                    # 原始工具返回（开发调试展开看）；截断防 SSE 过大
                    content_str = (
                        raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                    )
                    events.append(
                        {
                            "type": "tool_result",
                            "name": getattr(m, "name", "") or "",
                            "content": content_str[:2000],
                        }
                    )
                    for c in extract_cite(raw):
                        _add_cite(state, c)
    return events


def _answer_event(state):
    return {"type": "answer", "text": "".join(state["final"]), "sources": state["sources"]}


def events_from_stream(stream):
    """把同步 agent.stream(stream_mode=['updates','messages']) 映射成前端事件 tool/chunk/answer。"""
    state = _new_state()
    for mode, data in stream:
        yield from _map_event(mode, data, state)
    yield _answer_event(state)


async def events_from_astream(astream):
    """异步版：消费 agent.astream(...)，避免在 LLM 流式期间阻塞事件循环。"""
    state = _new_state()
    async for mode, data in astream:
        for ev in _map_event(mode, data, state):
            yield ev
    yield _answer_event(state)

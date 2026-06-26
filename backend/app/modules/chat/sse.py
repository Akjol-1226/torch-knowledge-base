import json
import re

from app.core.logging import get_logger

log = get_logger("chat.sse")

# 正文里的引用标记 [[cite:<handle>]]，与前端渲染用的正则一致
_CITE_RE = re.compile(r"\[\[cite:([^\]]+)\]\]")


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


def _new_state(seed_cites=None):
    # sources: 按文档分组的列表；doc_index: doc_id -> 该分组对象（便于追加 node）；
    # seen: 已收录 node 的去重键集合；read_ids: 本轮真正 read_node 读过正文的 handle 集合
    # （只有读过的才算"数据来源"，确保引用基于全文而非 search 片段）
    # seed_cites：纠正轮注入"服务端已补读"的节点 → 算"已读"，可合法引用并进数据来源。
    state = {"final": [], "sources": [], "seen": set(), "doc_index": {}, "read_ids": set()}
    for c in seed_cites or []:
        _add_cite(state, c)
        if c.get("handle"):
            state["read_ids"].add(c["handle"])
    return state


def _is_read_node_result(tool_name: str, raw) -> bool:
    """该工具结果是否是 read_node 读到的正文——只有它的 cite 才可作为最终引用来源。

    优先按工具名判定；ToolMessage 缺 name 时按结果形状兜底：read_node 返回【单个含 cite 的
    dict】，search_nodes 返回 list、错误返回不含 cite 的 dict——都不算。
    """
    if tool_name:
        return tool_name == "read_node"
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return False
    return isinstance(data, dict) and isinstance(data.get("cite"), dict)


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
                    tool_name = getattr(m, "name", "") or ""
                    # 原始工具返回（开发调试展开看）；截断防 SSE 过大
                    content_str = (
                        raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                    )
                    events.append(
                        {
                            "type": "tool_result",
                            "name": tool_name,
                            "content": content_str[:2000],
                        }
                    )
                    # 只有 read_node 读到的正文才计入"数据来源"：确保最终引用必须基于实际读过的
                    # 全文，而非 search_nodes 的片段。搜到但没 read 的节点不进 sources，其 [[cite]]
                    # 上标在前端因 handle 不在 sources 被丢弃（见 _answer_event 的未读引用校验）。
                    if _is_read_node_result(tool_name, raw):
                        for c in extract_cite(raw):
                            _add_cite(state, c)
                            if c.get("handle"):
                                state["read_ids"].add(c["handle"])
    return events


def _answer_event(state):
    text = "".join(state["final"])
    # 校验引用：正文 [[cite:id]] 必须是本轮 read_node 实际读过的 handle。
    # 未读就引用 = 模型据 search 片段编引用，前端会因 handle 不在 sources 丢弃该上标；
    # 这里记一条结构化告警便于监控这种"未据原文"的引用。
    cited = set(_CITE_RE.findall(text))
    ungrounded = sorted(cited - state["read_ids"])
    if ungrounded:
        log.warning(
            "chat_ungrounded_citations",
            count=len(ungrounded),
            cited=len(cited),
            ids=ungrounded[:10],
        )
    # ungrounded 透出给 router：非空则触发"补读未读节点→重写一次"的接地纠正
    return {"type": "answer", "text": text, "sources": state["sources"], "ungrounded": ungrounded}


def build_grounding_correction(ungrounded_ids: list) -> tuple:
    """为接地纠正轮服务端补读"被引用但本轮未 read_node 读过"的节点。

    返回 (seed_cites, corrective_text)：
    - seed_cites 注入纠正轮 state（_new_state(seed_cites=...)）→ 这些节点算"已读"，
      纠正答案可合法引用并进数据来源；
    - corrective_text 作为一条 user 消息回灌，附上能读到的正文，要求模型据此重写、删无据内容。
    读不到的 id（多半模型凭空标的）只在指令里要求删除相关内容。
    """
    from app.modules.chat.tools import get_store

    store = get_store()
    seed_cites: list = []
    blocks: list = []
    missing: list = []
    for hid in ungrounded_ids:
        try:
            node = store.read_node(hid)
        except Exception:
            node = None
        ok = isinstance(node, dict) and not node.get("error")
        if not ok or not isinstance(node.get("cite"), dict):
            missing.append(hid)
            continue
        cite = dict(node["cite"])
        cite["handle"] = cite.get("handle") or hid
        cite["doc_id"] = cite.get("doc_id") or hid.split(":")[0]
        text = node.get("text", "")
        cite["snippet"] = text[:200]
        seed_cites.append(cite)
        blocks.append(f"【{hid}｜{node.get('title', '')}】\n{text[:1800]}")

    parts = [
        "【系统·引用核对】你上一条回答给下面这些节点标了引用，但本轮你并没有用 read_node "
        "读过它们的正文，这是不允许的：",
        "、".join(ungrounded_ids),
    ]
    if blocks:
        parts.append("\n以下是其中能读到的节点正文，请据此核对：\n" + "\n\n".join(blocks))
    if missing:
        parts.append(
            "\n这些 id 在库里读不到（多半是凭空标的），相关内容必须删除：" + "、".join(missing)
        )
    parts.append(
        "\n请重写上一条回答："
        "① 删掉这些正文并不支撑的内容（尤其跨节点搬来的参数、对不上的引用、读不到的 id）；"
        "② 只保留确有正文支撑的内容；"
        "③ **每条文档事实句都必须保留 / 补上内联引用 [[cite:id]]，绝不能因为重写就丢掉引用**——"
        "缺引用的事实句一律视为无效；"
        "④ 若答案是流程图 / 表格 / 图这类不便逐句内联标注的形式，必须在开头或结尾单独写一行"
        "「数据来源：[[cite:id1]][[cite:id2]]…」把用到的全部节点列出，不得因为是图就不标来源；"
        "⑤ 不要再引用任何未读过正文的节点。直接输出修订后的完整回答。"
    )
    return seed_cites, "\n".join(parts)


def events_from_stream(stream, seed_cites=None):
    """把同步 agent.stream(stream_mode=['updates','messages']) 映射成前端事件 tool/chunk/answer。"""
    state = _new_state(seed_cites)
    for mode, data in stream:
        yield from _map_event(mode, data, state)
    yield _answer_event(state)


async def events_from_astream(astream, seed_cites=None):
    """异步版：消费 agent.astream(...)，避免在 LLM 流式期间阻塞事件循环。

    seed_cites：接地纠正轮把"服务端已补读"的节点预置进 state，使其计入 read_ids/数据来源。"""
    state = _new_state(seed_cites)
    async for mode, data in astream:
        for ev in _map_event(mode, data, state):
            yield ev
    yield _answer_event(state)

import json
import re
from typing import Any

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
    # read_cites: 本轮（含上一轮带过来的 seed）据正文读过的 cite 原始 dict，供接地纠正轮
    #   把"已读来源"整体带入下一轮——否则重写时来源清零、第0轮辛苦读到的源会丢（见 router）。
    # seed_cites：纠正轮注入"上一轮已读 + 服务端已补读"的节点 → 算"已读"，可合法引用并进数据来源。
    state = {"final": [], "sources": [], "seen": set(), "doc_index": {},
             "read_ids": set(), "read_cites": []}
    for c in seed_cites or []:
        _add_cite(state, c)
        h = c.get("handle")
        if h and h not in state["read_ids"]:   # 按 handle 去重，避免重复带入 read_cites
            state["read_ids"].add(h)
            state["read_cites"].append(c)
    return state


def _add_cite(state, cite: dict) -> bool:
    """把一个 cite dict 按文档分组、node 按 handle 去重累积进 state['sources']。
    cite 缺 handle/doc_id 时（旧数据/手构）用 doc+section+lines 兜底去重、用 doc 名分组。"""
    handle = cite.get("handle") or "·".join(
        (cite.get("doc", ""), cite.get("section", ""), cite.get("lines", ""))
    )
    if handle in state["seen"]:
        return False
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
    return True


def _map_event(event, state):
    """把 RuntimeEvent 映射成前端事件，并累积最终答案和引用。"""
    events = []
    event_type = event.get("type")
    if event_type == "assistant_chunk":
        text = event.get("text") or ""
        if text:
            state["final"].append(text)
            events.append({"type": "chunk", "text": text})
    elif event_type == "tool_call":
        events.append(
            {
                "type": "tool",
                "name": event.get("name") or "",
                "args": event.get("args") or {},
            }
        )
    elif event_type == "assistant_complete":
        if event.get("has_tool_calls"):
            # 调用工具轮产生的文字只是过程前言，不得进入终态答案和会话持久化。
            state["final"].clear()
    elif event_type == "tool_result":
        raw = event.get("content", "")
        tool_name = event.get("name") or ""
        content_str = (
            raw
            if isinstance(raw, str)
            else json.dumps(raw, ensure_ascii=False, default=str)
        )
        events.append(
            {
                "type": "tool_result",
                "name": tool_name,
                "content": content_str[:2000],
            }
        )
        # 只有成功 read_node 读到的正文才计入数据来源。
        if tool_name == "read_node" and not event.get("is_error", False):
            for cite in extract_cite(raw):
                _add_cite(state, cite)
                handle = cite.get("handle")
                if handle and handle not in state["read_ids"]:
                    state["read_ids"].add(handle)
                    state["read_cites"].append(cite)
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
    # read_cites 透出给 router：供纠正轮把本轮已读来源带进下一轮（router 下发前会剔除，不进前端）
    return {"type": "answer", "text": text, "sources": state["sources"],
            "ungrounded": ungrounded, "read_cites": state["read_cites"]}


def _read_grounding_cites(
    ungrounded_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    from app.modules.chat.tools import get_store

    store = get_store()
    seed_cites: list[dict[str, Any]] = []
    missing: list[str] = []
    for hid in ungrounded_ids:
        try:
            node = store.read_node(hid)
        except Exception:
            node = None
        if not isinstance(node, dict) or node.get("error"):
            missing.append(hid)
            continue
        raw_cite = node.get("cite")
        if not isinstance(raw_cite, dict):
            missing.append(hid)
            continue
        cite: dict[str, Any] = dict(raw_cite)
        cite["handle"] = cite.get("handle") or hid
        cite["doc_id"] = cite.get("doc_id") or hid.split(":")[0]
        text = node.get("text", "")
        cite["snippet"] = text[:200]
        cite["_grounding_title"] = node.get("title", "")
        cite["_grounding_text"] = text
        seed_cites.append(cite)
    return seed_cites, missing


def _strip_cite_markers(text: str, handles: list) -> str:
    for hid in handles:
        text = text.replace(f"[[cite:{hid}]]", "")
    return text


def autofix_grounding(final_ev: dict, ungrounded_ids: list, max_autofix: int = 3) -> dict | None:
    """Lightweight grounding repair for a few ungrounded cite ids.

    Readable ids are treated as server-verified sources. Missing ids have their cite marker
    removed. Larger batches fall back to the stricter LLM rewrite path.
    """
    if not ungrounded_ids or len(ungrounded_ids) > max_autofix:
        return None
    seed_cites, missing = _read_grounding_cites(ungrounded_ids)
    visible_seed_cites = [
        {k: v for k, v in cite.items() if not k.startswith("_grounding_")}
        for cite in seed_cites
    ]
    state = _new_state((final_ev.get("read_cites") or []) + visible_seed_cites)
    fixed = dict(final_ev)
    fixed["text"] = _strip_cite_markers(final_ev.get("text") or "", missing)
    fixed["sources"] = state["sources"]
    fixed["read_cites"] = state["read_cites"]
    fixed["ungrounded"] = []
    fixed["grounding_autofixed"] = True
    fixed["grounding_missing"] = missing
    fixed["grounding_read_count"] = len(ungrounded_ids)
    return fixed


def build_grounding_correction(ungrounded_ids: list) -> tuple:
    """为接地纠正轮服务端补读"被引用但本轮未 read_node 读过"的节点。

    返回 (seed_cites, corrective_text)：
    - seed_cites 注入纠正轮 state（_new_state(seed_cites=...)）→ 这些节点算"已读"，
      纠正答案可合法引用并进数据来源；
    - corrective_text 作为一条 user 消息回灌，附上能读到的正文，要求模型据此重写、删无据内容。
    读不到的 id（多半模型凭空标的）只在指令里要求删除相关内容。
    """
    seed_cites, missing = _read_grounding_cites(ungrounded_ids)
    blocks: list = []
    clean_seed_cites: list = []
    for cite in seed_cites:
        title = cite.pop("_grounding_title", "")
        text = cite.pop("_grounding_text", "")
        clean_seed_cites.append(cite)
        blocks.append(f"【{cite.get('handle')}｜{title}】\n{text[:1800]}")

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
    return clean_seed_cites, "\n".join(parts)


async def events_from_runtime(stream, seed_cites=None):
    """消费 Native Runtime 事件并映射为稳定的前端 SSE 事件。

    seed_cites：接地纠正轮把"服务端已补读"的节点预置进 state，使其计入 read_ids/数据来源。"""
    state = _new_state(seed_cites)
    async for event in stream:
        for ev in _map_event(event, state):
            yield ev
    yield _answer_event(state)

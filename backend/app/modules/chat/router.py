"""chat 模块路由（搬自 pageindex-agent/kb_agent/web/app.py）。

只暴露 POST /chat/stream：SSE 流式吐 tool / chunk / answer / error 事件。
多轮上下文来自前端传入的 history（无服务端 checkpointer，见 agent.build_agent）。
"""

import json
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.chat import conversation_service
from app.modules.chat.agent import build_agent
from app.modules.chat.sse import events_from_astream
from app.modules.chat.tools import current_kbs, get_store

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("chat")

# 请求体上限：单条消息字符数、保留的历史轮数、单条历史字符数
MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_ITEMS = 40
MAX_HISTORY_ITEM_CHARS = 8000

_agent = None


def get_agent():
    """懒构造 agent：首次请求时才建，避免无凭证时拖垮启动。"""
    global _agent
    if _agent is None:
        get_settings().apply_litellm_env()  # 把 LiteLLM Proxy 凭证桥接给底层（建树/对话共用）
        _agent = build_agent()
    return _agent


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    thread_id: str | None = None
    conversation_id: str | None = None  # 会话持久化 id（前端生成）；带上则流结束后存本轮
    history: list | None = None
    kbs: list[str] | None = None  # 检索范围：限定知识库名列表，None=全库


def _build_messages(req: "ChatRequest") -> list:
    """前端 history（[{role,content}]）+ 本轮 message 拼成对话；历史做有界裁剪。"""
    msgs: list = []
    history = (req.history or [])[-MAX_HISTORY_ITEMS:]
    for h in history:
        role = h.get("role") if isinstance(h, dict) else None
        content = h.get("content", "") if isinstance(h, dict) else ""
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": str(content)[:MAX_HISTORY_ITEM_CHARS]})
    msgs.append({"role": "user", "content": req.message})
    return msgs


@router.post("")
async def chat_once(req: ChatRequest) -> JSONResponse:
    """非流式问答（调试 / 简单集成用）；前端对话页走 /chat/stream。"""
    token = current_kbs.set(req.kbs)  # 检索范围隔离（工具读 contextvar）；务必配对 reset
    try:
        result = await get_agent().ainvoke({"messages": _build_messages(req)})
        answer = result["messages"][-1].content
        return JSONResponse({"answer": answer, "sources": []})
    except Exception:
        log.exception("chat_failed")
        return JSONResponse({"error": "服务出错，请稍后重试"}, status_code=500)
    finally:
        current_kbs.reset(token)


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    turn = uuid.uuid4().hex[:8]

    async def gen():
        token = current_kbs.set(req.kbs)  # 检索范围隔离（工具读 contextvar）；务必配对 reset
        log.info("chat_query", turn=turn, turns=len(req.history or []), msg=req.message[:200])
        answer_text = ""
        answer_sources: list = []
        try:
            try:
                astream = get_agent().astream(
                    {"messages": _build_messages(req)},
                    stream_mode=["updates", "messages"],
                )
                async for ev in events_from_astream(astream):
                    if ev.get("type") == "answer":
                        answer_text = ev.get("text") or answer_text
                        answer_sources = ev.get("sources") or answer_sources
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except Exception:
                log.exception("chat_stream_failed", turn=turn)
                # 独立 error 事件，前端按错误处理；不可发 answer 假装回答完成
                err = {"type": "error", "text": "服务出错，请稍后重试"}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                return
            # 流正常结束后持久化本轮（仅当前端带了 conversation_id）
            if req.conversation_id and answer_text:
                try:
                    await run_in_threadpool(
                        conversation_service.append_turn,
                        req.conversation_id,
                        req.message,
                        answer_text,
                        answer_sources,
                    )
                except Exception:
                    log.exception("conv_persist_failed", turn=turn)
        finally:
            current_kbs.reset(token)  # 配对 set，防 contextvar 跨请求残留

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/node")
async def node_context(handle: str) -> JSONResponse:
    """取某节点的上下文：本节点 + 相邻（优先同名窗口 section.span，否则 prev/本/next）。
    供前端「点开数据来源」时展示这一段的上下文，并高亮被引用的本节点（is_cited=true）。
    """

    def _build():
        store = get_store()
        node = store.read_node(handle)
        if "error" in node:
            return None
        sec = node.get("section") or {}
        span = sec.get("span")
        handles = list(span) if span else [
            h for h in (node.get("prev_id"), handle, node.get("next_id")) if h
        ]
        # 兜底：被点的 handle 必须在上下文里，否则前端会没有任何 is_cited 高亮
        if handle not in handles:
            handles.insert(0, handle)
        ctx = []
        for h in handles:
            n = store.read_node(h)
            if "error" in n:
                continue
            cite = n.get("cite", {})
            ctx.append(
                {
                    "handle": h,
                    "section": n.get("title", ""),
                    "lines": cite.get("lines", ""),
                    "page": cite.get("page"),
                    "text": n.get("text", ""),
                    "is_cited": h == handle,
                }
            )
        cited_cite = node.get("cite", {})
        doc_id = cited_cite.get("doc_id", "")
        page = cited_cite.get("page")
        # OCR 高亮:用被引用节点正文(= 数据来源展示的上下文)匹配 PDF 各页文字框 → 原文画框
        rects: list = []
        if doc_id and page:
            from app.modules.ingest import document_service, ocr_locate, page_locator

            md_path = document_service.get_md_path(doc_id)
            ocr = ocr_locate.load_ocr(md_path) if md_path else None
            if ocr:
                def _pages_for(cite: dict) -> list:
                    # 据节点行范围算 PDF 页跨度（跨多页时逐页；拿不到则退回该节点单页）
                    parts = (cite.get("lines") or "").split("-")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        span = page_locator.page_span(md_path, int(parts[0]), int(parts[1]))
                        if span:
                            return span
                    pg = cite.get("page")
                    return [pg] if pg else [page]

                # 匹配源：被引用节点正文 + 其直接子节点(附表/附件)正文，各自限定在自己的页
                # → 表格/附表的内容(挂在子节点上)也能被框住，而不只是被引用节点自己那一小段
                sources = [(node.get("text", ""), _pages_for(cited_cite))]
                for ch in store.open_node(handle).get("children", []):
                    cn = store.read_node(ch["id"])
                    if "error" not in cn:
                        sources.append((cn.get("text", ""), _pages_for(cn.get("cite", {}))))
                rects = ocr_locate.rects_for_node(ocr, sources)
        return {
            "handle": handle,
            "doc_name": cited_cite.get("doc", ""),
            "doc_id": doc_id,
            "page": page,  # 被引用节点所在 PDF 页（前端「原文 PDF」跳页用）
            "path": node.get("path", ""),  # 引用链/溯源面包屑：文档 > 章 > 节 > 当前节点
            "rects": rects,  # 被引用处高亮框（页内归一化坐标）
            "context": ctx,
        }

    data = await run_in_threadpool(_build)
    if data is None:
        return JSONResponse({"error": "节点不存在"}, status_code=404)
    return JSONResponse(data)


@router.get("/conversations")
async def list_conversations() -> JSONResponse:
    """会话列表（id/title/updated_at/message_count，倒序）。"""
    items = await run_in_threadpool(conversation_service.list_conversations)
    return JSONResponse(items)


@router.get("/conversations/{cid}")
async def get_conversation(cid: str) -> JSONResponse:
    """取单会话全部消息（加载历史用）。"""
    rec = await run_in_threadpool(conversation_service.get_conversation, cid)
    if rec is None:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return JSONResponse(rec)


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str) -> JSONResponse:
    """删除会话。"""
    ok = await run_in_threadpool(conversation_service.delete_conversation, cid)
    return JSONResponse({"deleted": ok})


class RenameRequest(BaseModel):
    title: str = Field(..., max_length=80)


@router.patch("/conversations/{cid}")
async def rename_conversation(cid: str, req: RenameRequest) -> JSONResponse:
    """重命名会话。"""
    ok = await run_in_threadpool(conversation_service.rename_conversation, cid, req.title)
    return JSONResponse({"renamed": ok})

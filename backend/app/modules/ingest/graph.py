"""入库流水线状态图（占位实现）。

v0 骨架：节点全部 print/log 自己被调用，便于联调测通框架。
真实实现（PDF 拆页 + VLM 抽取 + PageIndex 构建 + DB 持久化）等 ADR-002
（VLM 厂商）拍板 + PoC 完成后再补，挂到本图的同名节点上。
"""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger

logger = get_logger("ingest.graph")


class IngestState(TypedDict):
    document_id: int
    pages_done: int
    pages_total: int
    errors: list[str]
    trace: list[str]  # 调试用：记录走过的节点


async def _placeholder_load_pdf(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="load_pdf", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "load_pdf"]}


async def _placeholder_extract_pages(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="extract_pages", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "extract_pages"]}


async def _placeholder_build_pageindex(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="build_pageindex", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "build_pageindex"]}


async def _placeholder_persist(state: IngestState) -> IngestState:
    logger.info("ingest_node", node="persist", document_id=state["document_id"])
    return {**state, "trace": [*state["trace"], "persist"]}


def build_ingest_graph() -> Any:
    g: StateGraph = StateGraph(IngestState)
    g.add_node("load_pdf", _placeholder_load_pdf)
    g.add_node("extract_pages", _placeholder_extract_pages)
    g.add_node("build_pageindex", _placeholder_build_pageindex)
    g.add_node("persist", _placeholder_persist)

    g.set_entry_point("load_pdf")
    g.add_edge("load_pdf", "extract_pages")
    g.add_edge("extract_pages", "build_pageindex")
    g.add_edge("build_pageindex", "persist")
    g.add_edge("persist", END)
    return g.compile()

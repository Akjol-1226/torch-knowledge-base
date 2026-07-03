from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.retrieval import TreeStore
from app.core.retrieval.store_provider import get_tree_store, set_tree_store_for_tests

# 当前请求的检索范围；chat router 负责 set/reset。
current_kbs: ContextVar[list | None] = ContextVar("current_kbs", default=None)


def get_store() -> TreeStore:
    return get_tree_store()


def set_store(ts: TreeStore) -> None:
    set_tree_store_for_tests(ts)


def _hide_members(result):
    items = result if isinstance(result, list) else [result]
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("section"), dict):
            it["section"].pop("members", None)
    return result


@tool
def list_catalog() -> list:
    """列出知识库里的文档，返回 [{doc_id, doc_name, doc_description}]。"""
    return get_store().list_catalog(kbs=current_kbs.get())


@tool
def get_outline(doc_id: str) -> dict:
    """查看某文档顶层大纲。doc_id 来自 list_catalog。"""
    return get_store().get_outline(doc_id)


@tool
def open_node(node_id: str) -> dict:
    """展开某节点的直接子节点，只看结构和摘要，不读取正文。"""
    return get_store().open_node(node_id)


@tool
def read_node(node_id: str) -> dict:
    """读取某章节正文；如有 section.span，回答完整性问题前应继续读取 span 中节点。"""
    return _hide_members(get_store().read_node(node_id))


@tool
def search_nodes(query: str, top_k: int = 8) -> list | dict:
    """按内容检索相关章节节点；返回 dict 且含 error 时表示检索索引不可用。"""
    result = get_store().search_nodes(query, top_k=top_k, kbs=current_kbs.get())
    return _hide_members(result) if isinstance(result, list) else result


KB_TOOLS = [list_catalog, get_outline, open_node, read_node, search_nodes]

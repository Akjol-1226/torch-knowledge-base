from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.config import get_settings
from app.core.retrieval import TreeStore

# 当前请求的检索范围（知识库名列表）；None = 全库。由 chat router 按请求设置，
# 工具读取后传给 TreeStore 做 kb 过滤——实现"对话检索范围"隔离。
current_kbs: ContextVar[list | None] = ContextVar("current_kbs", default=None)

_store = None
_store_sig = None


def _catalog_sig():
    """用 catalog 文件 mtime 作为索引版本签名——入库/审核/删除重建后会重写它。"""
    f = get_settings().data_dir / "catalog" / "document_catalog.json"
    return f.stat().st_mtime if f.exists() else None


def get_store() -> TreeStore:
    """惰性单例；catalog 变更（入库/删除/审核重建）时自动重载，避免检索到旧树。"""
    global _store, _store_sig
    sig = _catalog_sig()
    if _store is None or sig != _store_sig:
        _store = TreeStore(get_settings().data_dir)
        _store_sig = sig
    return _store


def set_store(ts: TreeStore) -> None:
    global _store, _store_sig
    _store = ts
    _store_sig = _catalog_sig()  # 锁定当前签名，避免被 mtime 检测覆盖（测试注入用）


@tool
def list_catalog() -> list:
    """列出知识库里有哪些文档。返回 [{doc_id, doc_name, doc_description}]。
    不知道库里有哪些文档、或该看哪本时先调它。"""
    return get_store().list_catalog(kbs=current_kbs.get())


@tool
def get_outline(doc_id: str) -> dict:
    """看某文档的顶层章节大纲。doc_id 来自 list_catalog。
    返回 {doc, name, nodes:[{id, title, summary, lines, has_children}]}；
    has_children=true 的节点可用 open_node 继续下钻。返回含 error 字段则 doc_id 无效。"""
    return get_store().get_outline(doc_id)


@tool
def open_node(node_id: str) -> dict:
    """展开某节点的直接子章节（只看结构、不读正文）。返回 {node, title, children:[{id,title,summary,lines,has_children}]}。
    node_id 必须原样传回工具结果里给你的句柄，不要自己拼造。返回含 error 字段则 id 无效，请改用 search_nodes/list_catalog 重新定位。"""
    return get_store().open_node(node_id)


@tool
def read_node(node_id: str) -> dict:
    """读某章节正文。返回 {id, title, text, cite:{doc,section,lines}, path, parent_id, prev_id, next_id, has_children,
    section?}。其中 cite 用于写引用；parent_id/prev_id/next_id 用于看上级/相邻章节；
    若带 section:{part,total,span}，说明本节点只是"第 part/共 total 段"，完整内容（含附表等子节点）在 span 列出的所有句柄里——
    回答完整性问题前要把 span 里的句柄逐个 read_node 读全。node_id 原样传回；返回含 error 字段则 id 无效，改用 search_nodes 重新定位。"""
    return get_store().read_node(node_id)


@tool
def search_nodes(query: str, top_k: int = 8) -> list | dict:
    """按内容检索最相关的章节节点（BM25），细节/针尖问题、不知道在哪本哪节、跨文档查找时用。
    返回 [{id, title, score, snippet, cite, path, parent_id, prev_id, next_id, section?}]；命中后视情况用 read_node 读正文。
    若某命中带 section.span，整段已被折叠成这一条代表，完整内容在 span 列出的句柄里。
    注意：返回的是 dict 且含 error 字段时，表示检索索引不可用（不是"没搜到"），不要据此回答"库里没有"。"""
    return get_store().search_nodes(query, top_k=top_k, kbs=current_kbs.get())


KB_TOOLS = [list_catalog, get_outline, open_node, read_node, search_nodes]

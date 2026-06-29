import threading
from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.config import get_settings
from app.core.retrieval import TreeStore

# 当前请求的检索范围（知识库名列表）；None = 全库。由 chat router 按请求设置，
# 工具读取后传给 TreeStore 做 kb 过滤——实现"对话检索范围"隔离。
current_kbs: ContextVar[list | None] = ContextVar("current_kbs", default=None)

_store = None
_store_sig = None
_store_lock = threading.Lock()  # 防多线程（工具跑在线程池）并发重复重建 / 撕裂赋值


def _catalog_sig():
    """索引版本签名：catalog + 向量索引元数据的 mtime。

    catalog 在入库/审核/删除重建后会重写；vec_meta 在 /build-vectors 补建向量后会重写
    （此时 catalog 不变）——两者都纳入签名，确保补建向量后热服务也能重载到新索引。
    """
    d = get_settings().data_dir
    cat = d / "catalog" / "document_catalog.json"
    vec = d / "indexes" / "vec_meta.json"
    return (
        cat.stat().st_mtime if cat.exists() else None,
        vec.stat().st_mtime if vec.exists() else None,
    )


def get_store() -> TreeStore:
    """惰性单例；catalog/向量索引变更时增量刷新，避免检索到旧树。加锁防并发重复重建。

    变更时不再全量重建：首篇走 TreeStore 全量加载，之后用 incremental_clone 仅重读
    新增/变更/删除的文档（未变文档节点复用），构建好新实例后原子发布（不就地改，避免并发撕裂）。
    """
    global _store, _store_sig
    sig = _catalog_sig()
    if _store is not None and sig == _store_sig:
        return _store
    with _store_lock:
        # 双检：拿锁期间可能已被其他线程重建
        if _store is None or sig != _store_sig:
            if _store is None:
                store = TreeStore(get_settings().data_dir)  # 首次全量加载
            else:
                store = _store.incremental_clone()  # 增量刷新后发布新实例
            _store = store
            _store_sig = sig
    return _store


def set_store(ts: TreeStore) -> None:
    global _store, _store_sig
    with _store_lock:
        _store = ts
        _store_sig = _catalog_sig()  # 锁定当前签名，避免被 mtime 检测覆盖（测试注入用）


def _hide_members(result):
    """从透给 LLM 的工具结果里摘掉 section.members —— 它仅供 /node 前端「查看结构」显示窗口
    正文用；agent 要回答完整性问题必须读 section.span（含附表等子节点），别盯着 members 漏读。"""
    items = result if isinstance(result, list) else [result]
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("section"), dict):
            it["section"].pop("members", None)
    return result


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
    return _hide_members(get_store().read_node(node_id))


@tool
def search_nodes(query: str, top_k: int = 8) -> list | dict:
    """按内容检索最相关的章节节点（BM25），细节/针尖问题、不知道在哪本哪节、跨文档查找时用。
    返回 [{id, title, score, snippet, cite, path, parent_id, prev_id, next_id, section?}]；命中后视情况用 read_node 读正文。
    若某命中带 section.span，整段已被折叠成这一条代表，完整内容在 span 列出的句柄里。
    注意：返回的是 dict 且含 error 字段时，表示检索索引不可用（不是"没搜到"），不要据此回答"库里没有"。"""
    result = get_store().search_nodes(query, top_k=top_k, kbs=current_kbs.get())
    return _hide_members(result) if isinstance(result, list) else result


KB_TOOLS = [list_catalog, get_outline, open_node, read_node, search_nodes]

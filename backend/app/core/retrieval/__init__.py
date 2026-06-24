"""检索底座（搬自 pageindex-agent/kb_agent）：PageIndex 文档树仓储 + BM25 节点索引 + 中文分词。

跨模块共用：chat 模块用 TreeStore 做导航/检索，ingest 模块用 build_index/iter_nodes 建索引。
"""

from .bm25_index import BM25Index, build_index
from .embed import get_embed_client, set_embed_client
from .fusion import rrf_fuse
from .nodes import iter_nodes
from .snippet import make_snippet
from .tokenize import load_dict, write_domain_terms
from .treestore import TreeStore
from .vector_index import VectorIndex

# 注意：不在此导出 tokenize 函数——它与子模块 `tokenize` 同名，导出会遮蔽子模块，
# 让 `import app.core.retrieval.tokenize as tk` 拿到函数而非模块。需要分词函数时用
# `from app.core.retrieval.tokenize import tokenize`。
__all__ = [
    "TreeStore",
    "BM25Index",
    "build_index",
    "iter_nodes",
    "make_snippet",
    "load_dict",
    "write_domain_terms",
    "VectorIndex",
    "rrf_fuse",
    "get_embed_client",
    "set_embed_client",
]

import asyncio

from app.core.config import get_settings
from app.core.pageindex.page_index_md import md_to_tree


def build_tree(md_path: str, model: str) -> dict:
    """从 .md 建 PageIndex 树（LLM 生成节点摘要+文档描述）。

    返回 {doc_name, doc_description, line_count, structure}。

    瘦身（tree_thinning_min_tokens > 0）：把整棵子树过小的父节点的子节点正文并入自身，
    减少 VLM 标题切出的碎节点，让节点更密、更可检索。
    """
    min_tok = get_settings().tree_thinning_min_tokens
    coro = md_to_tree(
        md_path=md_path,
        if_thinning=min_tok > 0,
        min_token_threshold=min_tok,
        if_add_node_summary="yes",
        summary_token_threshold=200,
        model=model,
        if_add_doc_description="yes",
        if_add_node_text="yes",
        if_add_node_id="yes",
    )
    return asyncio.run(coro)

import asyncio

from app.core.pageindex.page_index_md import md_to_tree


def build_tree(md_path: str, model: str) -> dict:
    """从 .md 建 PageIndex 树（LLM 生成节点摘要+文档描述）。

    返回 {doc_name, doc_description, line_count, structure}。
    """
    coro = md_to_tree(
        md_path=md_path,
        if_add_node_summary="yes",
        summary_token_threshold=200,
        model=model,
        if_add_doc_description="yes",
        if_add_node_text="yes",
        if_add_node_id="yes",
    )
    return asyncio.run(coro)

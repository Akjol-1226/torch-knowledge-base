def iter_nodes(doc_id: str, tree: dict):
    """深度遍历树，产出每个节点的检索记录（句柄 = <doc_id>:<node_id>）。

    每条记录带 context = 文档名 + 祖先标题链（不含自身标题，自身在 title）。
    把这条面包屑并入 embed/BM25 文本，让「3.2 检验要求」这种泛标题节点带上
    所属文档与上级章节语境，避免不同文档里的同名节点向量/词项几乎不可区分。
    """
    doc_name = tree.get("doc_name", "")

    def walk(nodes, ancestors):
        for n in nodes:
            title = n.get("title", "")
            yield {
                "node_id_full": f"{doc_id}:{n['node_id']}",
                "title": title,
                "summary": n.get("summary", "") or "",
                "text": n.get("text", "") or "",
                "context": " > ".join(p for p in ([doc_name, *ancestors]) if p),
            }
            if n.get("nodes"):
                yield from walk(n["nodes"], [*ancestors, title])

    yield from walk(tree.get("structure", []), [])

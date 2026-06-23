def iter_nodes(doc_id: str, tree: dict):
    """深度遍历树，产出每个节点的检索记录（句柄 = <doc_id>:<node_id>）。"""
    def walk(nodes):
        for n in nodes:
            yield {
                "node_id_full": f"{doc_id}:{n['node_id']}",
                "title": n.get("title", ""),
                "summary": n.get("summary", "") or "",
                "text": n.get("text", "") or "",
            }
            if n.get("nodes"):
                yield from walk(n["nodes"])
    yield from walk(tree.get("structure", []))

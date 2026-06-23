from app.modules.ingest.graph import IngestState, build_ingest_graph


async def test_graph_runs_through_all_nodes() -> None:
    graph = build_ingest_graph()
    initial: IngestState = {
        "document_id": 1,
        "pages_done": 0,
        "pages_total": 10,
        "errors": [],
        "trace": [],
    }
    final = await graph.ainvoke(initial)

    assert final["trace"] == [
        "load_pdf",
        "extract_pages",
        "build_pageindex",
        "persist",
    ]
    assert final["errors"] == []


async def test_graph_state_preserved() -> None:
    graph = build_ingest_graph()
    initial: IngestState = {
        "document_id": 42,
        "pages_done": 0,
        "pages_total": 5,
        "errors": [],
        "trace": [],
    }
    final = await graph.ainvoke(initial)
    assert final["document_id"] == 42
    assert final["pages_total"] == 5

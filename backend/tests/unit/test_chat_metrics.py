from app.modules.chat.metrics import ChatTurnMetrics


def test_chat_turn_metrics_counts_runtime_events():
    metrics = ChatTurnMetrics(backend="native")
    metrics.observe({"type": "tool", "name": "search_nodes"})
    metrics.observe({"type": "tool", "name": "read_node"})
    metrics.observe({"type": "chunk", "text": "答案"})
    metrics.observe(
        {
            "type": "answer",
            "text": "答案正文",
            "sources": [{"nodes": [{"handle": "a"}, {"handle": "b"}]}],
            "ungrounded": ["c"],
        }
    )

    snapshot = metrics.snapshot()
    assert snapshot["backend"] == "native"
    assert snapshot["tool_calls"] == 2
    assert snapshot["search_nodes_calls"] == 1
    assert snapshot["read_node_calls"] == 1
    assert snapshot["answer_chars"] == 4
    assert snapshot["source_nodes"] == 2
    assert snapshot["ungrounded"] == 1
    assert snapshot["first_model_event_ms"] is not None
    assert snapshot["first_visible_text_ms"] is not None
    assert snapshot["final_answer_ready_ms"] is not None


def test_chat_turn_metrics_keeps_grounding_repair_out_of_model_tool_count():
    metrics = ChatTurnMetrics(backend="native")
    metrics.observe(
        {
            "type": "tool",
            "name": "核对引用",
            "origin": "grounding_repair",
            "read_count": 4,
        }
    )

    snapshot = metrics.snapshot()
    assert snapshot["tool_calls"] == 0
    assert snapshot["grounding_repairs"] == 1
    assert snapshot["grounding_reads"] == 4

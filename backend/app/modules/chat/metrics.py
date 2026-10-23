"""与具体 agent 运行时无关的单轮对话性能指标。"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class ChatTurnMetrics:
    backend: str
    started_at: float = field(default_factory=perf_counter)
    first_model_event_at: float | None = None
    first_chunk_at: float | None = None
    answer_ready_at: float | None = None
    tool_calls: int = 0
    read_node_calls: int = 0
    search_nodes_calls: int = 0
    grounding_repairs: int = 0
    grounding_reads: int = 0
    answer_chars: int = 0
    source_nodes: int = 0
    ungrounded: int = 0
    persistence_error: bool = False

    def observe(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type in {"tool", "chunk", "answer"} and self.first_model_event_at is None:
            self.first_model_event_at = perf_counter()
        if event_type == "chunk" and self.first_chunk_at is None:
            self.first_chunk_at = perf_counter()
        elif event_type == "tool":
            if event.get("origin") == "grounding_repair":
                self.grounding_repairs += 1
                self.grounding_reads += int(event.get("read_count") or 0)
                return
            self.tool_calls += 1
            name = event.get("name")
            self.read_node_calls += int(name == "read_node")
            self.search_nodes_calls += int(name == "search_nodes")
        elif event_type == "answer":
            self.answer_ready_at = perf_counter()
            self.answer_chars = len(event.get("text") or "")
            self.source_nodes = sum(
                len(group.get("nodes") or []) for group in (event.get("sources") or [])
            )
            self.ungrounded = len(event.get("ungrounded") or [])
            if event.get("grounding_autofixed"):
                self.grounding_repairs += 1
                self.grounding_reads += int(event.get("grounding_read_count") or 0)

    def snapshot(self, *, error: bool = False) -> dict:
        ended_at = perf_counter()
        return {
            "backend": self.backend,
            "total_ms": round((ended_at - self.started_at) * 1000, 2),
            "first_model_event_ms": (
                round((self.first_model_event_at - self.started_at) * 1000, 2)
                if self.first_model_event_at is not None
                else None
            ),
            "first_visible_text_ms": (
                round((self.first_chunk_at - self.started_at) * 1000, 2)
                if self.first_chunk_at is not None
                else None
            ),
            "final_answer_ready_ms": (
                round((self.answer_ready_at - self.started_at) * 1000, 2)
                if self.answer_ready_at is not None
                else None
            ),
            "tool_calls": self.tool_calls,
            "read_node_calls": self.read_node_calls,
            "search_nodes_calls": self.search_nodes_calls,
            "grounding_repairs": self.grounding_repairs,
            "grounding_reads": self.grounding_reads,
            "answer_chars": self.answer_chars,
            "source_nodes": self.source_nodes,
            "ungrounded": self.ungrounded,
            "persistence_error": self.persistence_error,
            "error": error,
        }

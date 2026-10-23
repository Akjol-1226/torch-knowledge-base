"""Bounded, local, optional observations; never part of the client SSE protocol.

Snapshots contain private model/tool content. Callers must keep them in private
artifacts. No external tracing service, application credentials or eval package
is needed by this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from threading import RLock
from time import perf_counter
from typing import Any


def _encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class TurnObservation:
    """One execution, one recorder. Limits or serialization errors invalidate trace.

    Methods deliberately swallow ordinary observation errors, retaining only
    stable reason codes. Observations cannot turn a successful answer into an
    application error. Missing observations must not become a complete trace.
    """

    def __init__(
        self, run_id: str, *, max_events: int = 10_000,
        max_bytes: int = 32 * 1024 * 1024, clock: Callable[[], float] = perf_counter,
    ) -> None:
        if not run_id.strip() or max_events < 2 or max_bytes < 1024:
            raise ValueError("invalid observation configuration")
        self.run_id = run_id
        self._lock = RLock()
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._clock = clock
        self._started = clock()
        self._events: list[dict[str, Any]] = []
        self._snapshots: dict[str, Any] = {}
        self._bytes = 0
        self._failures: set[str] = set()
        self._terminal = False

    def invalidate(self, reason: str = "observer_error") -> None:
        # Do not retain arbitrary exception strings, which may contain content.
        with self._lock:
            self._failures.add(reason if reason in {
                "event_limit", "byte_limit", "observer_error", "late_event", "duplicate_terminal"
            } else "observer_error")

    def snapshot(self, value: Any) -> str | None:
        with self._lock:
            return self._snapshot(value)

    def _snapshot(self, value: Any) -> str | None:
        try:
            if self._terminal:
                self.invalidate("late_event")
                return None
            encoded = _encode(value)
            digest = hashlib.sha256(encoded).hexdigest()
            if digest not in self._snapshots:
                stored_bytes = len(encoded) + len(digest) + 6
                if self._bytes + stored_bytes > self._max_bytes:
                    self.invalidate("byte_limit")
                    return None
                # Store a copy, not a reference later mutated by model/tool code.
                self._snapshots[digest] = json.loads(encoded)
                self._bytes += stored_bytes
            return digest
        except Exception:
            self.invalidate()
            return None

    def emit(self, kind: str, **payload: Any) -> None:
        with self._lock:
            self._emit(kind, **payload)

    def _emit(self, kind: str, **payload: Any) -> None:
        try:
            if self._terminal:
                self.invalidate("late_event")
                return
            if len(self._events) >= self._max_events:
                self.invalidate("event_limit")
                return
            sequence = len(self._events) + 1
            event = {
                "observation_schema_version": 1, "run_id": self.run_id,
                "event_id": f"{self.run_id}:{sequence}", "sequence": sequence,
                "kind": kind, "elapsed_ms": (self._clock() - self._started) * 1000,
                "data": payload,
            }
            encoded = _encode(event)
            if self._bytes + len(encoded) > self._max_bytes:
                self.invalidate("byte_limit")
                return
            self._events.append(json.loads(encoded))
            self._bytes += len(encoded) + 1
        except Exception:
            self.invalidate()

    def terminal(self, status: str) -> None:
        with self._lock:
            if self._terminal:
                self.invalidate("duplicate_terminal")
                return
            self.emit("turn_terminal", execution_status=status)
            self._terminal = True

    def bundle(self) -> dict[str, Any]:
        with self._lock:
            return self._bundle()

    def _bundle(self) -> dict[str, Any]:
        status = "complete" if self._terminal and not self._failures else "incomplete"
        return {
            "observation_schema_version": 1, "run_id": self.run_id,
            "artifact_status": status,
            "reason_codes": sorted(self._failures | (set() if self._terminal else {"no_terminal"})),
            "events": json.loads(_encode(self._events)),
            "snapshots": json.loads(_encode(self._snapshots)),
        }


def invalidate(recorder: TurnObservation) -> None:
    try:
        recorder.invalidate()
    except Exception:
        # Even a custom health reporter is outside the answer's failure path.
        return


def observe(recorder: TurnObservation | None, kind: str, **payload: Any) -> None:
    """Also isolate a broken/custom recorder implementation from the business path."""
    if recorder is None:
        return
    try:
        recorder.emit(kind, **payload)
    except Exception:
        invalidate(recorder)


def snapshot(recorder: TurnObservation | None, value: Any) -> str | None:
    if recorder is None:
        return None
    try:
        return recorder.snapshot(value)
    except Exception:
        invalidate(recorder)
        return None


def finish(recorder: TurnObservation | None, status: str) -> None:
    if recorder is None:
        return
    try:
        recorder.terminal(status)
    except Exception:
        invalidate(recorder)


def tool_outcome(content: Any, *, is_error: bool) -> tuple[str, str]:
    """Distinguish an invocation error from a returned domain error object."""
    if is_error:
        return "error", "error"
    value = content
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return "ok", "unknown"
    if isinstance(value, dict):
        return "ok", "error" if value.get("error") else "ok"
    if isinstance(value, list):
        return "ok", "error" if any(
            isinstance(item, dict) and item.get("error") for item in value
        ) else "ok"
    return "ok", "unknown"


def model_input(
    recorder: TurnObservation | None, messages: list[Any], *,
    repair_pass: int, round_index: int, model_call_id: str,
) -> None:
    if recorder is None:
        return
    try:
        frozen = [message.model_dump(mode="json") for message in messages]
        observe(
            recorder, "model_started", repair_pass=repair_pass, round_index=round_index,
            model_call_id=model_call_id, input_snapshot=snapshot(recorder, frozen),
            physical_attempts=None,
        )
        for message in frozen:
            if message.get("type") != "tool":
                continue
            observe(
                recorder, "included_in_model_input", repair_pass=repair_pass,
                round_index=round_index, model_call_id=model_call_id,
                tool_call_id=message.get("tool_call_id"), tool_name=message.get("name"),
                content_snapshot=snapshot(recorder, message.get("content")),
            )
    except Exception:
        invalidate(recorder)


def grounding_read(
    recorder: TurnObservation | None, kind: str, handle: str, text: str,
    *, repair_pass: int, **metadata: Any,
) -> None:
    if recorder is None:
        return
    try:
        observe(
            recorder, kind, repair_pass=repair_pass, handle=handle,
            **metadata,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text_snapshot=snapshot(recorder, text),
        )
    except Exception:
        invalidate(recorder)

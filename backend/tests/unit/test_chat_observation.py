from __future__ import annotations

import hashlib
import json

import pytest

from app.modules.chat.observation import TurnObservation, finish, observe, snapshot, tool_outcome


def test_observation_copies_snapshots_and_events_and_hashes_actual_bytes():
    ticks = iter([1.0, 1.1, 1.2])
    recorder = TurnObservation("run-1", clock=lambda: next(ticks))
    source = {"text": "虚构正文"}
    digest = recorder.snapshot(source)
    recorder.emit("model_started", nested=source)
    source["text"] = "changed"
    recorder.terminal("completed")
    bundle = recorder.bundle()
    assert bundle["artifact_status"] == "complete"
    assert bundle["snapshots"][digest]["text"] == "虚构正文"
    assert bundle["events"][0]["data"]["nested"]["text"] == "虚构正文"
    encoded = json.dumps(
        bundle["snapshots"][digest], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == digest
    assert bundle["events"][0]["elapsed_ms"] == pytest.approx(100)
    bundle["events"].clear()
    assert len(recorder.bundle()["events"]) == 2


@pytest.mark.parametrize("failure", ["count", "bytes", "serialization", "no_terminal", "late"])
def test_lost_observations_cannot_be_complete(failure):
    recorder = TurnObservation("bounded", max_events=2, max_bytes=1024)
    if failure == "count":
        for _ in range(3):
            recorder.emit("event")
    elif failure == "bytes":
        recorder.snapshot("x" * 2048)
    elif failure == "serialization":
        recorder.emit("event", content=object())
    if failure != "no_terminal":
        recorder.terminal("completed")
    if failure == "late":
        recorder.emit("after_terminal")
    assert recorder.bundle()["artifact_status"] == "incomplete"
    assert recorder.bundle()["reason_codes"]


def test_broken_optional_observer_does_not_raise_into_business_path():
    class Broken(TurnObservation):
        def emit(self, *args, **kwargs):
            raise RuntimeError("private text")

        def snapshot(self, value):
            raise RuntimeError("private text")

        def terminal(self, status):
            raise RuntimeError("private text")

    recorder = Broken("broken")
    observe(recorder, "event")
    assert snapshot(recorder, {"content": "x"}) is None
    finish(recorder, "completed")
    assert recorder.bundle()["artifact_status"] == "incomplete"
    assert "private text" not in json.dumps(recorder.bundle())
    observe(None, "event")
    finish(None, "completed")


def test_broken_health_reporter_cannot_raise_into_business_path():
    class Broken(TurnObservation):
        def emit(self, *args, **kwargs):
            raise RuntimeError("emit failed")

        def snapshot(self, value):
            raise RuntimeError("snapshot failed")

        def terminal(self, status):
            raise RuntimeError("terminal failed")

        def invalidate(self, reason="observer_error"):
            raise RuntimeError("health reporter also failed")

    recorder = Broken("broken-health")
    observe(recorder, "event")
    assert snapshot(recorder, "content") is None
    finish(recorder, "completed")


def test_snapshot_after_terminal_invalidates_seal():
    recorder = TurnObservation("closed")
    recorder.terminal("completed")
    assert recorder.snapshot("late content") is None
    assert recorder.bundle()["artifact_status"] == "incomplete"
    assert recorder.bundle()["reason_codes"] == ["late_event"]


@pytest.mark.parametrize(("content", "is_error", "expected"), [
    ({"error": "index missing"}, False, ("ok", "error")),
    ('{"error":"missing"}', False, ("ok", "error")),
    ([{"error": "missing"}], False, ("ok", "error")),
    ({"text": "synthetic"}, False, ("ok", "ok")),
    ("opaque text", False, ("ok", "unknown")),
    ({}, True, ("error", "error")),
])
def test_tool_domain_and_transport_status_are_separate(content, is_error, expected):
    assert tool_outcome(content, is_error=is_error) == expected

"""Offline audit counterexamples use invented records, never business traces."""

from __future__ import annotations

import copy
import json

import pytest

from evals.agent_schemas import canonical_sha256, sha256_text
from evals.agent_trace import audit_trace


class Trace:
    def __init__(self):
        self.bundle = {
            "observation_schema_version": 1, "run_id": "fictional-local-trace",
            "artifact_status": "complete", "reason_codes": [], "events": [], "snapshots": {},
        }
        self.emit("turn_started")

    def freeze(self, value):
        digest = canonical_sha256(value)
        self.bundle["snapshots"][digest] = copy.deepcopy(value)
        return digest

    def emit(self, kind, **data):
        sequence = len(self.bundle["events"]) + 1
        event = {
            "observation_schema_version": 1, "run_id": self.bundle["run_id"],
            "event_id": f"{self.bundle['run_id']}:{sequence}", "sequence": sequence,
            "elapsed_ms": sequence * 2.0, "kind": kind, "data": data,
        }
        self.bundle["events"].append(event)
        return event

    def config(self, passage=0):
        self.emit(
            "runtime_config", repair_pass=passage, max_agent_rounds=25,
            max_tool_concurrency=2, prompt_snapshot=self.freeze("Fictional system."),
            tool_schema_snapshot=self.freeze([
                {"type": "function", "function": {
                    "name": "echo", "description": "Fictional tool.",
                    "parameters": {"type": "object"},
                }},
            ]),
        )

    def model(self, messages, *, passage=0, round_index=1, calls=None, status="ok"):
        identity = {"repair_pass": passage, "round_index": round_index,
                    "model_call_id": f"p{passage}:r{round_index}"}
        self.emit("model_started", **identity, input_snapshot=self.freeze(messages))
        for message in messages:
            if message["type"] == "tool":
                self.emit(
                    "included_in_model_input", **identity,
                    tool_call_id=message["tool_call_id"], tool_name=message["name"],
                    content_snapshot=self.freeze(message["content"]),
                )
        output = {"type": "ai", "content": "Done.", "tool_calls": calls or []}
        self.emit(
            "model_finished", **identity, status=status, invoke_wall_ms=1.0,
            output_snapshot=self.freeze(output if status == "ok" else None),
        )
        return output

    def tool(self, call, *, round_index=1, result=None):
        identity = {
            "repair_pass": 0, "round_index": round_index, "tool_call_id": call["id"],
            "tool_name": call["name"],
        }
        self.emit("tool_proposed", **identity, args_snapshot=self.freeze(call["args"]))
        self.emit("tool_queued", **identity)
        self.emit("tool_started", **identity, semaphore_wait_ms=0.5)
        self.emit(
            "tool_finished", **identity, transport_status="ok", domain_status="ok",
            invoke_wall_ms=1.0, semaphore_wait_ms=0.5, result_snapshot=self.freeze(result),
        )
        self.emit("tool_result_emitted", **identity)

    def finish(self, status="completed"):
        if status == "completed":
            self.emit("turn_final_answer", answer_snapshot=self.freeze({"text": "Done."}))
            self.emit("final_cited", handles=[])
        self.emit("turn_terminal", execution_status=status)
        return self.bundle


def seed():
    return [
        {"type": "system", "content": "Fictional system."},
        {"type": "human", "content": "Fictional question."},
    ]


def basic():
    trace = Trace()
    trace.config()
    trace.model(seed())
    trace.finish()
    return trace


def tool_turn(*, reused=False):
    trace = Trace()
    trace.config()
    messages = seed()
    for round_index in range(1, 3 if reused else 2):
        call = {"id": "same-provider-id", "name": "echo", "args": {"value": round_index}}
        output = trace.model(messages, round_index=round_index, calls=[call])
        result = json.dumps({"value": round_index})
        trace.tool(call, round_index=round_index, result=result)
        messages = messages + [output, {
            "type": "tool", "content": result, "tool_call_id": call["id"], "name": "echo",
        }]
    trace.model(messages, round_index=3 if reused else 2)
    trace.finish()
    return trace


def correction_turn():
    trace = Trace()
    trace.config()
    trace.model(seed())
    trace.emit("grounding_repair_started", repair_pass=0)
    trace.emit("grounding_repair_finished", repair_pass=0)
    trace.emit("grounding_repair_started", repair_pass=1)
    body = "Fictional node text." + "x" * 2000
    for kind, text in (("correction_read", body), ("correction_input_evidence", body[:1800])):
        trace.emit(
            kind, repair_pass=1, handle="fictional:1", text_snapshot=trace.freeze(text),
            text_sha256=sha256_text(text),
        )
    trace.emit("grounding_repair_finished", repair_pass=1)
    trace.config(1)
    trace.model(seed() + [{
        "type": "human", "content": "Verify:\n【fictional:1｜Title】\n" + body[:1800],
    }], passage=1)
    trace.finish()
    return trace


def event(trace, kind, index=0):
    return [item for item in trace.bundle["events"] if item["kind"] == kind][index]


def remove(trace, kind):
    trace.bundle["events"] = [item for item in trace.bundle["events"] if item["kind"] != kind]
    for sequence, item in enumerate(trace.bundle["events"], start=1):
        item["sequence"] = sequence
        item["event_id"] = f"{trace.bundle['run_id']}:{sequence}"


@pytest.mark.parametrize("build", [basic, tool_turn, lambda: tool_turn(reused=True), correction_turn])
def test_complete_internally_consistent_trace_is_accepted(build):
    result = audit_trace(build().bundle)
    assert result["execution_status"] == "completed"
    assert result["scope"] == "local_boundary_and_snapshot_audit_not_execution_authentication"


@pytest.mark.parametrize("status", ["error", "cancelled", "timeout"])
def test_noncompleted_trace_can_close_without_a_final_answer(status):
    trace = Trace()
    trace.config()
    trace.model(seed(), status="cancelled" if status == "cancelled" else "error")
    assert audit_trace(trace.finish(status))["execution_status"] == status


@pytest.mark.parametrize("kind", [
    "runtime_config", "model_started", "model_finished", "turn_final_answer", "final_cited",
    "turn_terminal", "turn_started",
])
def test_removing_required_boundary_is_rejected_even_after_renumbering(kind):
    trace = basic()
    remove(trace, kind)
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("mutation", [
    "negative-clock", "nonfinite-clock", "boolean-clock", "boolean-version", "gap",
    "changed-snapshot", "missing-snapshot", "late-event", "unknown-kind", "nested-snapshot",
])
def test_envelope_clock_and_snapshot_failures_are_rejected(mutation):
    trace = basic()
    first = trace.bundle["events"][0]
    if mutation == "negative-clock":
        first["elapsed_ms"] = -0.01
    elif mutation == "nonfinite-clock":
        first["elapsed_ms"] = float("nan")
    elif mutation == "boolean-clock":
        first["elapsed_ms"] = True
    elif mutation == "boolean-version":
        first["observation_schema_version"] = True
    elif mutation == "gap":
        first["sequence"] = 2
    elif mutation == "changed-snapshot":
        trace.bundle["snapshots"][next(iter(trace.bundle["snapshots"]))] = "changed"
    elif mutation == "missing-snapshot":
        event(trace, "model_started")["data"]["input_snapshot"] = "0" * 64
    elif mutation == "late-event":
        trace.emit("turn_started")
    elif mutation == "unknown-kind":
        first["kind"] = "unrecognized"
    else:
        first["data"]["nested"] = [{"hidden_snapshot": "0" * 64}]
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("mutation", ["prompt", "schema", "capture-error", "round-limit"])
def test_configuration_cannot_disagree_with_actual_model_input(mutation):
    trace = basic()
    config = event(trace, "runtime_config")["data"]
    if mutation == "prompt":
        config["prompt_snapshot"] = trace.freeze("A different prompt.")
    elif mutation == "schema":
        config["tool_schema_snapshot"] = trace.freeze({"not": "a schema list"})
    elif mutation == "capture-error":
        config["capture_status"] = "error"
    else:
        config["max_agent_rounds"] = True
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("mutation", ["args", "name", "round", "id", "extra-model-call"])
def test_tool_proposal_must_match_its_own_model_output(mutation):
    trace = tool_turn()
    data = event(trace, "tool_proposed")["data"]
    if mutation == "args":
        data["args_snapshot"] = trace.freeze({"value": 999})
    elif mutation == "name":
        data["tool_name"] = "other_tool"
    elif mutation == "round":
        data["round_index"] = 2
    elif mutation == "id":
        data["tool_call_id"] = "invented-call"
    else:
        finished = event(trace, "model_finished")["data"]
        output = copy.deepcopy(trace.bundle["snapshots"][finished["output_snapshot"]])
        output["tool_calls"].append({"id": "unobserved", "name": "echo", "args": {}})
        finished["output_snapshot"] = trace.freeze(output)
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("kind", ["tool_proposed", "tool_queued", "tool_started", "tool_finished",
                                  "tool_result_emitted", "included_in_model_input"])
def test_missing_tool_boundaries_cannot_be_rescued_by_a_complete_terminal(kind):
    trace = tool_turn()
    remove(trace, kind)
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("mutation", ["content", "name", "id", "model", "passage", "round"])
def test_input_inclusion_is_verified_against_messages_not_just_a_valid_snapshot_hash(mutation):
    trace = tool_turn()
    data = event(trace, "included_in_model_input")["data"]
    if mutation == "content":
        data["content_snapshot"] = trace.freeze("A different validly hashed result.")
    elif mutation == "name":
        data["tool_name"] = "wrong_name"
    elif mutation == "id":
        data["tool_call_id"] = "wrong_id"
    elif mutation == "model":
        data["model_call_id"] = "p0:r1"
    elif mutation == "passage":
        data["repair_pass"] = 1
    else:
        data["round_index"] = 1
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("mutation", ["fragment", "body-hash", "actual-input", "wrong-block",
                                      "missing-projection"])
def test_correction_projection_requires_original_prefix_and_actual_model_consumption(mutation):
    trace = correction_turn()
    data = event(trace, "correction_input_evidence")["data"]
    if mutation == "fragment":
        data["text_snapshot"] = trace.freeze("A fabricated fragment.")
        data["text_sha256"] = sha256_text("A fabricated fragment.")
    elif mutation == "body-hash":
        data["text_sha256"] = "0" * 64
    elif mutation == "missing-projection":
        remove(trace, "correction_input_evidence")
    else:
        model = event(trace, "model_started", 1)["data"]
        messages = copy.deepcopy(trace.bundle["snapshots"][model["input_snapshot"]])
        if mutation == "actual-input":
            messages[-1]["content"] = "No evidence was actually sent."
        else:
            messages[-1]["content"] = messages[-1]["content"].replace("fictional:1｜", "other:1｜")
        model["input_snapshot"] = trace.freeze(messages)
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


@pytest.mark.parametrize("value", [-1, True, float("inf"), None])
def test_negative_or_unknown_successful_invocation_duration_is_not_a_measured_duration(value):
    trace = tool_turn()
    event(trace, "tool_finished")["data"]["invoke_wall_ms"] = value
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


def test_final_citation_inventory_is_derived_from_the_final_text():
    trace = basic()
    event(trace, "final_cited")["data"]["handles"] = ["fictional:absent"]
    with pytest.raises(ValueError):
        audit_trace(trace.bundle)


def test_changing_input_and_inclusion_together_cannot_replace_the_real_tool_result():
    trace = tool_turn()
    model = event(trace, "model_started", 1)["data"]
    messages = copy.deepcopy(trace.bundle["snapshots"][model["input_snapshot"]])
    replacement = "A validly hashed but never returned result."
    messages[-1]["content"] = replacement
    model["input_snapshot"] = trace.freeze(messages)
    event(trace, "included_in_model_input")["data"]["content_snapshot"] = trace.freeze(replacement)
    with pytest.raises(ValueError, match="differs from emitted tool result"):
        audit_trace(trace.bundle)


def test_tool_result_batch_cannot_substitute_for_the_final_model_answer():
    trace = Trace()
    trace.config()
    call = {"id": "call-only", "name": "echo", "args": {}}
    trace.model(seed(), calls=[call])
    trace.tool(call, result="Fictional tool result.")
    trace.finish()
    with pytest.raises(ValueError, match="final answer lacks"):
        audit_trace(trace.bundle)


def test_valid_hash_cannot_replace_the_final_model_answer():
    trace = basic()
    event(trace, "turn_final_answer")["data"]["answer_snapshot"] = trace.freeze({
        "text": "A different answer with no model origin.",
    })
    with pytest.raises(ValueError, match="final answer differs"):
        audit_trace(trace.bundle)


def test_only_recorded_failed_autofix_citation_markers_can_be_removed():
    trace = Trace()
    trace.config()
    output = trace.model(seed())
    output["content"] = "Done.[[cite:fictional:missing]]"
    event(trace, "model_finished")["data"]["output_snapshot"] = trace.freeze(output)
    trace.emit("grounding_repair_started", repair_pass=0)
    trace.emit("grounding_read_failed", repair_pass=0, stage="server_autofix_read",
               handle="fictional:missing")
    trace.emit("grounding_repair_finished", repair_pass=0)
    trace.finish()
    assert audit_trace(trace.bundle)["execution_status"] == "completed"
    remove(trace, "grounding_read_failed")
    with pytest.raises(ValueError, match="final answer differs"):
        audit_trace(trace.bundle)

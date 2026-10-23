"""Offline consistency audit of local boundaries, never execution authentication."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from evals.agent_schemas import canonical_sha256, sha256_text

ToolKey = tuple[int, int, str]


@dataclass
class _Model:
    passage: int
    round_index: int
    messages: list[dict[str, Any]]
    expected_included: list[dict[str, Any]]
    included: int = 0
    status: str = "started"
    output: dict[str, Any] | None = None
    calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposed: set[str] = field(default_factory=set)


@dataclass
class _Tool:
    name: str
    state: str = "proposed"
    result: Any = None
    transport: str | None = None
    domain: str | None = None


def _integer(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid integer identity")
    return value


def _passage(data: dict[str, Any]) -> int:
    passage = _integer(data.get("repair_pass"))
    if passage not in (0, 1):
        raise ValueError("invalid repair passage")
    return passage


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid text identity")
    return value


def _milliseconds(value: Any) -> None:
    if type(value) not in (int, float) or value < 0:
        raise ValueError("invalid nonnegative duration")
    canonical_sha256(value)


def _reference(snapshots: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, str) or value not in snapshots:
        raise ValueError("missing content snapshot")
    return snapshots[value]


def _snapshot_references(value: Any, snapshots: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid event field")
            if key.endswith("_snapshot"):
                _reference(snapshots, item)
            else:
                _snapshot_references(item, snapshots)
    elif isinstance(value, list):
        for item in value:
            _snapshot_references(item, snapshots)


def _grounding_text(data: dict[str, Any], snapshots: dict[str, Any]) -> tuple[str, str]:
    handle = _text(data.get("handle"))
    text = _reference(snapshots, data.get("text_snapshot"))
    if not isinstance(text, str) or data.get("text_sha256") != sha256_text(text):
        raise ValueError("grounding text hash mismatch")
    return handle, text


def _tool_key(data: dict[str, Any]) -> ToolKey:
    return (
        _passage(data), _integer(data.get("round_index"), minimum=1),
        _text(data.get("tool_call_id")),
    )


def _model_identity(data: dict[str, Any], models: dict[str, _Model]) -> tuple[str, _Model]:
    call_id = _text(data.get("model_call_id"))
    model = models.get(call_id)
    if model is None or (
        _passage(data), _integer(data.get("round_index"), minimum=1)
    ) != (model.passage, model.round_index):
        raise ValueError("model call identity mismatch")
    return call_id, model


def _calls(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if output.get("invalid_tool_calls"):
        raise ValueError("successful model output has invalid tool calls")
    raw = output.get("tool_calls", [])
    if not isinstance(raw, list):
        raise ValueError("invalid model tool calls")
    calls: dict[str, dict[str, Any]] = {}
    for call in raw:
        if not isinstance(call, dict):
            raise ValueError("invalid model tool call")
        identity = _text(call.get("id"))
        _text(call.get("name"))
        if identity in calls or not isinstance(call.get("args"), dict):
            raise ValueError("ambiguous model tool call")
        calls[identity] = call
    return calls


def _correction_present(messages: list[dict[str, Any]], handle: str, fragment: str) -> bool:
    # Bind to the generated final user message and the handle's own block.
    user = next((message for message in reversed(messages) if message.get("type") == "human"), None)
    if user is None or not isinstance(user.get("content"), str):
        return False
    content = user["content"]
    marker = f"【{handle}｜"
    position = content.find(marker)
    if position < 0 or content.find(marker, position + len(marker)) >= 0:
        return False
    start = content.find("】\n", position + len(marker))
    return start >= 0 and content[start + 2:start + 2 + len(fragment)] == fragment


def audit_trace(bundle: dict[str, Any]) -> dict[str, Any]:
    """Reject lost events, unclosed boundaries and inconsistent frozen content.

    This audits a supplied local record. Its hashes cannot authenticate a party
    able to replace the entire record, and it does not certify HTTP persistence.
    """
    try:
        return _audit_trace(bundle)
    except (TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ValueError("invalid trace structure") from exc


def _audit_trace(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or set(bundle) != {
        "observation_schema_version", "run_id", "artifact_status", "reason_codes",
        "events", "snapshots",
    } or type(bundle["observation_schema_version"]) is not int:
        raise ValueError("invalid trace envelope")
    if bundle["observation_schema_version"] != 1 or bundle["artifact_status"] != "complete":
        raise ValueError("incomplete or unsupported trace")
    if bundle["reason_codes"] != [] or not isinstance(bundle["snapshots"], dict):
        raise ValueError("observation failure")
    run_id = _text(bundle["run_id"])
    events, snapshots = bundle["events"], bundle["snapshots"]
    if not isinstance(events, list):
        raise ValueError("invalid event collection")
    for digest, content in snapshots.items():
        if canonical_sha256(content) != digest:
            raise ValueError("snapshot hash mismatch")

    models: dict[str, _Model] = {}
    rounds: dict[tuple[int, int], str] = {}
    configurations: dict[int, dict[str, Any]] = {}
    tools: dict[ToolKey, _Tool] = {}
    active_repairs: set[int] = set()
    seen_repairs: set[int] = set()
    correction_reads: dict[str, str] = {}
    correction_fragments: dict[str, str] = {}
    autofix_missing: set[str] = set()
    previous_ms = 0.0
    terminal = None
    answer: str | None = None
    final_cited = False
    started = False

    for sequence, event in enumerate(events, start=1):
        if not isinstance(event, dict) or set(event) != {
            "observation_schema_version", "run_id", "event_id", "sequence", "kind",
            "elapsed_ms", "data",
        }:
            raise ValueError("invalid observation event")
        if (
            type(event["observation_schema_version"]) is not int
            or event["observation_schema_version"] != 1
            or type(event["sequence"]) is not int or event["sequence"] != sequence
            or event["event_id"] != f"{run_id}:{sequence}" or event["run_id"] != run_id
        ):
            raise ValueError("trace gap or identity mismatch")
        elapsed = event["elapsed_ms"]
        _milliseconds(elapsed)
        if elapsed < previous_ms:
            raise ValueError("trace clock moved backwards")
        canonical_sha256(event)
        previous_ms = elapsed
        if terminal is not None:
            raise ValueError("event after terminal")
        kind, data = event["kind"], event["data"]
        if not isinstance(data, dict):
            raise ValueError("invalid event data")
        if not started and kind != "turn_started":
            raise ValueError("turn start missing")
        if answer is not None and kind not in {"final_cited", "turn_terminal"}:
            raise ValueError("operation after final answer")
        _snapshot_references(data, snapshots)

        if kind == "turn_started":
            if started:
                raise ValueError("duplicate turn start")
            started = True
        elif kind == "runtime_config":
            passage = _passage(data)
            if passage in configurations or any(m.passage == passage for m in models.values()):
                raise ValueError("duplicate or late runtime configuration")
            if passage == 1 and 1 not in seen_repairs:
                raise ValueError("repair runtime without repair preparation")
            if data.get("capture_status") == "error":
                raise ValueError("runtime configuration capture failed")
            prompt = _reference(snapshots, data.get("prompt_snapshot"))
            schemas = _reference(snapshots, data.get("tool_schema_snapshot"))
            if not isinstance(prompt, str) or not isinstance(schemas, list):
                raise ValueError("invalid frozen runtime configuration")
            names = []
            for schema in schemas:
                function = schema.get("function") if isinstance(schema, dict) else None
                if not isinstance(function, dict) or schema.get("type") != "function" or (
                    not isinstance(function.get("parameters"), dict)
                ):
                    raise ValueError("invalid frozen OpenAI function schema")
                names.append(_text(function.get("name")))
            if len(names) != len(set(names)):
                raise ValueError("duplicate bound tool schema")
            _integer(data.get("max_agent_rounds"), minimum=1)
            _integer(data.get("max_tool_concurrency"), minimum=1)
            configurations[passage] = data
        elif kind == "model_started":
            call_id = _text(data.get("model_call_id"))
            passage, round_index = _passage(data), _integer(data.get("round_index"), minimum=1)
            if call_id in models or (passage, round_index) in rounds:
                raise ValueError("duplicate model call")
            config = configurations.get(passage)
            if config is None or round_index > config["max_agent_rounds"]:
                raise ValueError("model call lacks runtime configuration")
            if round_index != 1 + sum(m.passage == passage for m in models.values()):
                raise ValueError("model round gap")
            if active_repairs or any(m.status == "started" for m in models.values()):
                raise ValueError("overlapping model or repair boundaries")
            if round_index > 1:
                previous = models[rounds[(passage, round_index - 1)]]
                if previous.status != "ok" or not previous.calls or any(
                    tools.get((passage, round_index - 1, identity), _Tool("")).state != "emitted"
                    for identity in previous.calls
                ):
                    raise ValueError("model round before prior tool results")
            messages = _reference(snapshots, data.get("input_snapshot"))
            if not isinstance(messages, list) or not messages or any(
                not isinstance(message, dict) or "content" not in message for message in messages
            ) or messages[0].get("type") != "system":
                raise ValueError("invalid model input projection")
            if messages[0]["content"] != snapshots[config["prompt_snapshot"]]:
                raise ValueError("model system prompt differs from runtime configuration")
            if round_index > 1:
                previous = models[rounds[(passage, round_index - 1)]]
                prefix = previous.messages + [previous.output]
                if len(messages) != len(prefix) + len(previous.calls) or canonical_sha256(
                    messages[:len(prefix)]
                ) != canonical_sha256(prefix):
                    raise ValueError("next model input differs from prior frozen history")
                for message, (identity, call) in zip(
                    messages[len(prefix):], previous.calls.items(), strict=True
                ):
                    tool = tools[(passage, round_index - 1, identity)]
                    if (
                        message.get("type") != "tool" or message.get("tool_call_id") != identity
                        or message.get("name") != call["name"]
                        or canonical_sha256(message["content"]) != canonical_sha256(tool.result)
                    ):
                        raise ValueError("actual model input differs from emitted tool result")
            if passage == 1 and round_index == 1 and any(
                not _correction_present(messages, handle, fragment)
                for handle, fragment in correction_fragments.items()
            ):
                raise ValueError("correction evidence absent from actual model input")
            models[call_id] = _Model(
                passage, round_index, messages,
                [message for message in messages if message.get("type") == "tool"],
            )
            rounds[(passage, round_index)] = call_id
        elif kind == "included_in_model_input":
            _, model = _model_identity(data, models)
            if model.status != "started" or model.included >= len(model.expected_included):
                raise ValueError("extra or late included tool message")
            expected = model.expected_included[model.included]
            if (
                data.get("tool_call_id") != expected.get("tool_call_id")
                or data.get("tool_name") != expected.get("name")
                or data.get("content_snapshot") != canonical_sha256(expected["content"])
            ):
                raise ValueError("included tool message differs from frozen model input")
            model.included += 1
        elif kind == "model_finished":
            _, model = _model_identity(data, models)
            if model.status != "started" or model.included != len(model.expected_included):
                raise ValueError("unpaired model or missing input inclusion")
            status = data.get("status")
            if status not in {"ok", "error", "cancelled"}:
                raise ValueError("invalid model terminal")
            _milliseconds(data.get("invoke_wall_ms"))
            if status == "ok":
                output = _reference(snapshots, data.get("output_snapshot"))
                if not isinstance(output, dict) or output.get("type") != "ai":
                    raise ValueError("successful model output not frozen")
                model.output = output
                model.calls = _calls(output)
            model.status = status
        elif kind in {
            "tool_proposed", "tool_rejected", "tool_queued", "tool_started",
            "tool_finished", "tool_result_emitted",
        }:
            key = _tool_key(data)
            passage, round_index, identity = key
            model_id = rounds.get((passage, round_index))
            model = models.get(model_id or "")
            if model is None or model.status != "ok" or identity not in model.calls:
                raise ValueError("tool call absent from its model output")
            call = model.calls[identity]
            if "tool_name" in data and data["tool_name"] != call["name"]:
                raise ValueError("tool name differs from model output")
            tool = tools.get(key)
            if kind == "tool_proposed":
                if tool is not None or data.get("tool_name") != call["name"]:
                    raise ValueError("duplicate or mismatched tool proposal")
                if canonical_sha256(_reference(snapshots, data.get("args_snapshot"))) != (
                    canonical_sha256(call["args"])
                ):
                    raise ValueError("tool arguments differ from model output")
                tools[key] = _Tool(call["name"])
                model.proposed.add(identity)
                continue
            if tool is None:
                raise ValueError("tool boundary without proposal")
            if kind == "tool_rejected":
                if tool.state != "proposed" or not data.get("reason"):
                    raise ValueError("invalid rejected tool")
                tool.state = "rejected"
            elif kind == "tool_queued":
                if tool.state != "proposed":
                    raise ValueError("tool queued without proposal")
                tool.state = "queued"
            elif kind == "tool_started":
                if tool.state != "queued":
                    raise ValueError("tool start without queue")
                _milliseconds(data.get("semaphore_wait_ms"))
                tool.state = "started"
            elif kind == "tool_finished":
                if tool.state not in {"queued", "started"}:
                    raise ValueError("unpaired tool finish")
                transport, domain = data.get("transport_status"), data.get("domain_status")
                if transport not in {"ok", "error", "cancelled"} or domain not in {
                    "ok", "error", "unknown"
                }:
                    raise ValueError("invalid tool outcome")
                _milliseconds(data.get("semaphore_wait_ms"))
                if tool.state == "queued":
                    if (
                        transport == "ok" or not data.get("reason")
                        or data.get("invoke_wall_ms") is not None
                    ):
                        raise ValueError("tool invocation start missing")
                else:
                    _milliseconds(data.get("invoke_wall_ms"))
                tool.result = _reference(snapshots, data.get("result_snapshot"))
                tool.transport, tool.domain, tool.state = transport, domain, "finished"
            elif tool.state != "finished":
                raise ValueError("result emitted before tool finished")
            else:
                tool.state = "emitted"
        elif kind in {"search_returned", "tool_read_returned"}:
            tool = tools.get(_tool_key(data))
            expected_name = "search_nodes" if kind == "search_returned" else "read_node"
            if tool is None or tool.state != "finished" or tool.name != expected_name or (
                tool.transport, tool.domain
            ) != ("ok", "ok"):
                raise ValueError("read/search provenance lacks successful tool boundary")
            if kind == "search_returned":
                if data.get("content_snapshot") != canonical_sha256(tool.result):
                    raise ValueError("search provenance differs from actual tool result")
            else:
                handle, text = _grounding_text(data, snapshots)
                result = json.loads(tool.result) if isinstance(tool.result, str) else tool.result
                if not isinstance(result, dict) or result.get("text") != text:
                    raise ValueError("read provenance differs from actual tool result")
                cite = result.get("cite")
                if isinstance(cite, dict) and cite.get("handle") and cite["handle"] != handle:
                    raise ValueError("read provenance handle mismatch")
        elif kind in {"server_autofix_read", "correction_read", "correction_input_evidence"}:
            passage = _passage(data)
            expected_passage = 0 if kind == "server_autofix_read" else 1
            if passage not in active_repairs or passage != expected_passage:
                raise ValueError("grounding evidence outside its repair boundary")
            handle, text = _grounding_text(data, snapshots)
            if kind == "correction_read":
                if handle in correction_reads:
                    raise ValueError("duplicate correction read")
                correction_reads[handle] = text
            elif kind == "correction_input_evidence":
                if handle in correction_fragments or handle not in correction_reads or text != (
                    correction_reads[handle][:1800]
                ):
                    raise ValueError("correction fragment differs from its original read")
                correction_fragments[handle] = text
        elif kind == "grounding_repair_started":
            passage = _passage(data)
            if passage in seen_repairs or active_repairs or any(
                m.status == "started" for m in models.values()
            ):
                raise ValueError("duplicate or overlapping grounding repair")
            last = next(reversed(models.values()), None)
            if last is None or last.status != "ok" or last.calls or any(
                tool.state != "emitted" for tool in tools.values()
            ):
                raise ValueError("grounding repair before completed draft boundaries")
            seen_repairs.add(passage)
            active_repairs.add(passage)
        elif kind == "grounding_repair_finished":
            passage = _passage(data)
            if passage not in active_repairs:
                raise ValueError("unpaired grounding repair")
            active_repairs.remove(passage)
        elif kind == "grounding_read_failed":
            passage = _passage(data)
            stage = data.get("stage")
            if stage in {"server_autofix_read", "correction_read"}:
                expected_passage = 0 if stage == "server_autofix_read" else 1
                if passage != expected_passage or passage not in active_repairs:
                    raise ValueError("failed read outside its repair boundary")
                handle = _text(data.get("handle"))
                if stage == "server_autofix_read":
                    autofix_missing.add(handle)
        elif kind == "turn_final_answer":
            if answer is not None:
                raise ValueError("duplicate final answer")
            final = _reference(snapshots, data.get("answer_snapshot"))
            if not isinstance(final, dict) or not isinstance(final.get("text"), str):
                raise ValueError("invalid final answer projection")
            last = next(reversed(models.values()), None)
            if last is None or last.status != "ok" or last.calls or last.output is None:
                raise ValueError("final answer lacks a final model response")
            expected_text = last.output.get("content")
            if not isinstance(expected_text, str):
                raise ValueError("nontext model output lacks a supported text projection")
            for handle in autofix_missing:
                expected_text = expected_text.replace(f"[[cite:{handle}]]", "")
            if final["text"] != expected_text:
                raise ValueError("final answer differs from model output and recorded autofix")
            answer = final["text"]
        elif kind == "final_cited":
            if answer is None or final_cited or data.get("handles") != sorted(set(
                re.findall(r"\[\[cite:([^\]]+)\]\]", answer)
            )):
                raise ValueError("final citation inventory differs from final answer")
            final_cited = True
        elif kind == "turn_terminal":
            terminal = data.get("execution_status")
            if terminal not in {"completed", "error", "cancelled", "timeout"}:
                raise ValueError("invalid turn terminal")
        else:
            raise ValueError("unknown observation kind")

    if terminal is None or active_repairs or any(m.status == "started" for m in models.values()):
        raise ValueError("missing terminal boundary")
    if any(tool.state in {"proposed", "queued", "started"} for tool in tools.values()) or any(
        model.proposed != set(model.calls) for model in models.values()
    ):
        raise ValueError("unterminated tool call")
    if correction_reads.keys() != correction_fragments.keys():
        raise ValueError("correction read lacks its input projection")
    if correction_fragments and (1, 1) not in rounds:
        raise ValueError("prepared correction evidence has no actual model input")
    if terminal == "completed" and (
        answer is None or not final_cited or not models
        or any(model.status != "ok" for model in models.values())
        or any(tool.state != "emitted" for tool in tools.values())
        or next(reversed(models.values())).calls
    ):
        raise ValueError("completed turn lacks required successful boundaries")
    if terminal != "completed" and answer is not None:
        raise ValueError("noncompleted turn has a final answer")
    return {
        "execution_status": terminal, "model_calls": len(models), "tool_calls": len(tools),
        "scope": "local_boundary_and_snapshot_audit_not_execution_authentication",
    }

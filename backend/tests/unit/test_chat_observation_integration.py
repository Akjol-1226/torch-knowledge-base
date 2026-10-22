"""Exercise real Native/execute_turn boundaries with offline, fictional fakes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import tool

from app.modules.chat import tools as chat_tools
from app.modules.chat.execution import execute_turn
from app.modules.chat.native_runtime import AgentStepLimitError, NativeReactRuntime
from app.modules.chat.observation import TurnObservation
from evals.agent_schemas import canonical_sha256
from evals.agent_trace import audit_trace


class FakeModel:
    model_name = "offline-fictional-model"
    temperature = 0
    max_retries = 0

    def __init__(self, responses):
        self.responses = list(responses)
        self.inputs = []

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    async def astream(self, messages, config=None):
        self.inputs.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        chunks = response if isinstance(response, list) else [response]
        for chunk in chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            if isinstance(chunk, AIMessage):
                chunk = AIMessageChunk(
                    content=chunk.content, tool_calls=chunk.tool_calls,
                    response_metadata=chunk.response_metadata,
                )
            yield chunk


class FakeStore:
    def __init__(self, nodes):
        self.nodes = nodes
        self.reads = []

    def read_node(self, node_id):
        self.reads.append(node_id)
        return copy.deepcopy(self.nodes.get(node_id, {"error": "fictional missing node"}))


def node(handle, text):
    return {
        "title": "Fictional section", "text": text,
        "cite": {"handle": handle, "doc_id": "fictional", "doc": "Fictional fixture",
                 "section": handle, "lines": "1-2"},
    }


def call(name, args, call_id="call-1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def collect(runtime, observation=None, *, concurrency=2):
    return [event async for event in execute_turn(
        runtime, [{"role": "user", "content": "An entirely fictional question."}],
        {"max_concurrency": concurrency}, observation=observation,
    )]


def of_kind(bundle, kind):
    return [event for event in bundle["events"] if event["kind"] == kind]


def renumber(bundle):
    for index, event in enumerate(bundle["events"], start=1):
        event["sequence"] = index
        event["event_id"] = f"{bundle['run_id']}:{index}"


async def simple_tool_turn():
    @tool
    async def echo(value: str) -> dict:
        """Echo a fictional value without IO."""
        return {"value": value}

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("echo", {"value": "fixture"})]),
        AIMessage(content="Offline completion."),
    ])
    recorder = TurnObservation("offline-tool-trace")
    events = await collect(NativeReactRuntime(model, [echo], "Fictional system."), recorder)
    return events, recorder.bundle(), model


@pytest.mark.asyncio
async def test_real_execute_turn_tool_loop_yields_auditable_model_inputs_and_outputs():
    events, bundle, _ = await simple_tool_turn()
    assert events[-1]["text"] == "Offline completion."
    assert audit_trace(bundle)["execution_status"] == "completed"
    assert len(of_kind(bundle, "model_started")) == 2
    assert len(of_kind(bundle, "model_finished")) == 2
    included = of_kind(bundle, "included_in_model_input")
    assert len(included) == 1
    assert included[0]["data"]["tool_call_id"] == "call-1"
    assert "fixture" in bundle["snapshots"][included[0]["data"]["content_snapshot"]]


@pytest.mark.asyncio
async def test_fast_parallel_tool_is_finished_before_batch_emission_and_slow_tool():
    fast_done = asyncio.Event()

    @tool
    async def delayed(label: str) -> dict:
        """Delay with a known ordering, not a live service."""
        if label == "slow":
            await fast_done.wait()
            await asyncio.sleep(0.04)
        else:
            fast_done.set()
        return {"label": label}

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("delayed", {"label": "slow"}, "slow"),
                                         call("delayed", {"label": "fast"}, "fast")]),
        AIMessage(content="Done."),
    ])
    recorder = TurnObservation("parallel-tools")
    events = await collect(NativeReactRuntime(model, [delayed], "system"), recorder)
    assert events[-1]["text"] == "Done."
    bundle = recorder.bundle()
    finished = {event["data"]["tool_call_id"]: event for event in of_kind(bundle, "tool_finished")}
    assert finished["fast"]["elapsed_ms"] < finished["slow"]["elapsed_ms"]
    assert finished["fast"]["data"]["invoke_wall_ms"] < finished["slow"]["data"]["invoke_wall_ms"]
    emitted = of_kind(bundle, "tool_result_emitted")
    assert min(event["elapsed_ms"] for event in emitted) >= finished["slow"]["elapsed_ms"]
    assert audit_trace(bundle)["tool_calls"] == 2


@pytest.mark.asyncio
async def test_semaphore_queue_is_separate_from_each_tool_invocation_duration():
    @tool
    async def queued(label: str) -> dict:
        """Use a short deterministic delay to exercise a single-slot queue."""
        await asyncio.sleep(0.04 if label == "first" else 0)
        return {"label": label}

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("queued", {"label": "first"}, "first"),
                                         call("queued", {"label": "second"}, "second")]),
        AIMessage(content="Done."),
    ])
    recorder = TurnObservation("queued-tools")
    await collect(NativeReactRuntime(model, [queued], "system"), recorder, concurrency=1)
    bundle = recorder.bundle()
    finished = {event["data"]["tool_call_id"]: event["data"]
                for event in of_kind(bundle, "tool_finished")}
    assert finished["second"]["semaphore_wait_ms"] >= 20
    assert finished["second"]["invoke_wall_ms"] < finished["second"]["semaphore_wait_ms"]
    assert audit_trace(bundle)["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_domain_error_json_is_not_misreported_as_a_python_transport_exception():
    @tool
    async def search_nodes(query: str) -> str:
        """Return a fictional domain error as successful JSON transport."""
        return json.dumps({"error": "fictional index unavailable"})

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("search_nodes", {"query": "fixture"})]),
        AIMessage(content="There is insufficient evidence."),
    ])
    recorder = TurnObservation("domain-error")
    events = await collect(NativeReactRuntime(model, [search_nodes], "system"), recorder)
    data = of_kind(recorder.bundle(), "tool_finished")[0]["data"]
    assert data["transport_status"] == "ok"
    assert data["domain_status"] == "error"
    assert events[-1]["text"] == "There is insufficient evidence."
    assert audit_trace(recorder.bundle())["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_model_error_closes_real_model_boundary_and_turn_without_final_answer():
    recorder = TurnObservation("model-error")
    model = FakeModel([[AIMessageChunk(content="Partial."), RuntimeError("fictional failure")]])
    with pytest.raises(RuntimeError, match="fictional failure"):
        await collect(NativeReactRuntime(model, [], "system"), recorder)
    bundle = recorder.bundle()
    assert of_kind(bundle, "model_finished")[0]["data"]["status"] == "error"
    assert not of_kind(bundle, "turn_final_answer")
    assert audit_trace(bundle)["execution_status"] == "error"


@pytest.mark.asyncio
async def test_model_cancellation_propagates_and_closes_nested_generators():
    entered, closed = asyncio.Event(), asyncio.Event()

    class BlockingModel(FakeModel):
        async def astream(self, messages, config=None):
            self.inputs.append(list(messages))
            try:
                yield AIMessageChunk(content="Partial.")
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    recorder = TurnObservation("cancel-model")
    task = asyncio.create_task(collect(NativeReactRuntime(BlockingModel([]), [], "system"), recorder))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    bundle = recorder.bundle()
    assert of_kind(bundle, "model_finished")[0]["data"]["status"] == "cancelled"
    assert audit_trace(bundle)["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_consumer_close_closes_the_model_stream_before_returning():
    closed = asyncio.Event()

    class StreamModel(FakeModel):
        async def astream(self, messages, config=None):
            try:
                yield AIMessageChunk(content="Partial.")
                await asyncio.Event().wait()
            finally:
                closed.set()

    recorder = TurnObservation("consumer-close")
    stream = cast(AsyncGenerator[dict, None], execute_turn(
        NativeReactRuntime(StreamModel([]), [], "system"),
        [{"role": "user", "content": "Fictional question."}], {}, observation=recorder,
    ))
    assert (await anext(stream))["text"] == "Partial."
    await stream.aclose()
    assert closed.is_set()
    assert audit_trace(recorder.bundle())["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_running_and_queued_tools_have_terminal_observations():
    entered = asyncio.Event()

    @tool
    async def blocked(label: str) -> dict:
        """Block solely on a local event so cancellation is observable."""
        entered.set()
        await asyncio.Event().wait()
        return {"label": label}

    model = FakeModel([AIMessage(content="", tool_calls=[
        call("blocked", {"label": "running"}, "running"),
        call("blocked", {"label": "queued"}, "queued"),
    ])])
    recorder = TurnObservation("cancel-tools")
    task = asyncio.create_task(collect(NativeReactRuntime(model, [blocked], "system"), recorder,
                                       concurrency=1))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    bundle = recorder.bundle()
    assert len(of_kind(bundle, "tool_started")) == 1
    finished = of_kind(bundle, "tool_finished")
    assert len(finished) == 2
    assert all(event["data"]["transport_status"] == "cancelled" for event in finished)
    assert audit_trace(bundle)["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_step_cap_records_rejected_proposals_without_running_the_tool():
    invoked = []

    @tool
    async def echo(value: str) -> dict:
        """Must never be invoked after reaching the model step cap."""
        invoked.append(value)
        return {"value": value}

    model = FakeModel([AIMessage(content="", tool_calls=[call("echo", {"value": "x"})])])
    recorder = TurnObservation("step-cap")
    with pytest.raises(AgentStepLimitError):
        await collect(NativeReactRuntime(model, [echo], "system", max_agent_rounds=1), recorder)
    bundle = recorder.bundle()
    assert not invoked
    assert of_kind(bundle, "tool_rejected")[0]["data"]["reason"] == "step_limit"
    assert not of_kind(bundle, "tool_started")
    assert audit_trace(bundle)["execution_status"] == "error"


async def autofix_turn(monkeypatch):
    text = "FICTIONAL-FULL-BODY-" + "x" * 2200 + "-TAIL"
    store = FakeStore({"fictional:1": node("fictional:1", text)})
    monkeypatch.setattr(chat_tools, "get_store", lambda: store)
    model = FakeModel([AIMessage(content="A claim.[[cite:fictional:1]]")])
    recorder = TurnObservation("autofix-trace")
    events = await collect(NativeReactRuntime(model, [], "system"), recorder)
    return events, recorder.bundle(), model, text


@pytest.mark.asyncio
async def test_server_autofix_full_text_is_not_falsely_claimed_as_model_input(monkeypatch):
    events, bundle, model, text = await autofix_turn(monkeypatch)
    assert events[-1]["grounding_autofixed"] is True
    assert len(model.inputs) == 1
    reads = of_kind(bundle, "server_autofix_read")
    assert len(reads) == 1
    data = reads[0]["data"]
    assert bundle["snapshots"][data["text_snapshot"]] == text
    assert data["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    inputs = bundle["snapshots"][of_kind(bundle, "model_started")[0]["data"]["input_snapshot"]]
    assert "FICTIONAL-FULL-BODY" not in json.dumps(inputs)
    assert not of_kind(bundle, "correction_input_evidence")
    assert audit_trace(bundle)["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_failed_server_autofix_removes_only_the_recorded_missing_citation(monkeypatch):
    store = FakeStore({})
    monkeypatch.setattr(chat_tools, "get_store", lambda: store)
    model = FakeModel([AIMessage(content="Fictional claim.[[cite:fictional:missing]]")])
    recorder = TurnObservation("failed-autofix")
    events = await collect(NativeReactRuntime(model, [], "system"), recorder)
    assert events[-1]["text"] == "Fictional claim."
    assert events[-1]["grounding_missing"] == ["fictional:missing"]
    failed = of_kind(recorder.bundle(), "grounding_read_failed")
    assert failed[0]["data"]["stage"] == "server_autofix_read"
    assert audit_trace(recorder.bundle())["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_correction_pass_uses_only_recorded_1800_character_body_slices(monkeypatch):
    bodies = {f"fictional:{index}": f"BODY-{index}-" + "x" * 2200 + f"-TAIL-{index}"
              for index in range(1, 5)}
    store = FakeStore({handle: node(handle, body) for handle, body in bodies.items()})
    monkeypatch.setattr(chat_tools, "get_store", lambda: store)
    citations = "".join(f"[[cite:{handle}]]" for handle in bodies)
    model = FakeModel([AIMessage(content="Draft." + citations),
                       AIMessage(content="Corrected." + citations)])
    recorder = TurnObservation("correction-trace")
    events = await collect(NativeReactRuntime(model, [], "system"), recorder)
    bundle = recorder.bundle()
    assert events[-1]["text"] == "Corrected." + citations
    assert len(model.inputs) == 2
    assert not of_kind(bundle, "server_autofix_read")
    assert len(of_kind(bundle, "correction_read")) == 4
    input_events = of_kind(bundle, "correction_input_evidence")
    assert len(input_events) == 4
    second_model = next(event for event in of_kind(bundle, "model_started")
                        if event["data"]["repair_pass"] == 1)
    second_input = bundle["snapshots"][second_model["data"]["input_snapshot"]]
    corrective_text = second_input[-1]["content"]
    for event in input_events:
        data = event["data"]
        expected = bodies[data["handle"]][:1800]
        assert data["repair_pass"] == 1
        assert bundle["snapshots"][data["text_snapshot"]] == expected
        assert data["text_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
        assert expected in corrective_text
        assert bodies[data["handle"]] not in corrective_text
    assert audit_trace(bundle)["model_calls"] == 2


@pytest.mark.asyncio
async def test_read_node_body_is_linked_to_a_later_model_input_without_server_repair():
    body = "An entirely fictional read_node body."

    @tool
    async def read_node(node_id: str) -> dict:
        """Read a fictional in-memory node."""
        return node(node_id, body)

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("read_node", {"node_id": "fictional:1"})]),
        AIMessage(content="A claim.[[cite:fictional:1]]"),
    ])
    recorder = TurnObservation("read-node-trace")
    events = await collect(NativeReactRuntime(model, [read_node], "system"), recorder)
    bundle = recorder.bundle()
    assert events[-1]["ungrounded"] == []
    assert not of_kind(bundle, "server_autofix_read")
    read = of_kind(bundle, "tool_read_returned")[0]["data"]
    assert bundle["snapshots"][read["text_snapshot"]] == body
    included = of_kind(bundle, "included_in_model_input")[0]["data"]
    assert body in bundle["snapshots"][included["content_snapshot"]]
    assert audit_trace(bundle)["execution_status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["limit", "callbacks"])
async def test_observation_failures_never_change_the_actual_business_answer(failure):
    class Broken(TurnObservation):
        def emit(self, *args, **kwargs):
            raise RuntimeError("fictional callback failure")

        def snapshot(self, value):
            raise RuntimeError("fictional snapshot failure")

        def terminal(self, status):
            raise RuntimeError("fictional terminal failure")

    baseline = await collect(NativeReactRuntime(FakeModel([AIMessage(content="Done.")]), [], "sys"))
    recorder = (TurnObservation("overflow", max_events=2) if failure == "limit"
                else Broken("broken-callback"))
    observed = await collect(NativeReactRuntime(FakeModel([AIMessage(content="Done.")]), [], "sys"),
                             recorder)
    assert observed == baseline
    assert recorder.bundle()["artifact_status"] == "incomplete"
    with pytest.raises(ValueError):
        audit_trace(recorder.bundle())


@pytest.mark.asyncio
async def test_opaque_provider_metadata_invalidates_observation_without_breaking_answer():
    response = AIMessage(content="Done.", response_metadata={"opaque": object()})
    recorder = TurnObservation("opaque-provider-metadata")
    events = await collect(NativeReactRuntime(FakeModel([response]), [], "system"), recorder)
    assert events[-1]["text"] == "Done."
    assert recorder.bundle()["artifact_status"] == "incomplete"
    with pytest.raises(ValueError):
        audit_trace(recorder.bundle())


@pytest.mark.asyncio
async def test_reused_provider_call_id_in_later_round_has_distinct_internal_identity():
    @tool
    async def echo(value: str) -> dict:
        """Return an offline fixture value."""
        return {"value": value}

    model = FakeModel([
        AIMessage(content="", tool_calls=[call("echo", {"value": "one"}, "provider-reused")]),
        AIMessage(content="", tool_calls=[call("echo", {"value": "two"}, "provider-reused")]),
        AIMessage(content="Done."),
    ])
    recorder = TurnObservation("reused-provider-call")
    assert (await collect(NativeReactRuntime(model, [echo], "system"), recorder))[-1]["text"] == "Done."
    assert audit_trace(recorder.bundle())["tool_calls"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["snapshot", "gap", "model-output", "tool-started", "tool-emitted"])
async def test_audit_rejects_tampered_real_execution_trace(mutation):
    _, bundle, _ = await simple_tool_turn()
    assert audit_trace(bundle)["execution_status"] == "completed"
    if mutation == "snapshot":
        digest = next(iter(bundle["snapshots"]))
        bundle["snapshots"][digest] = {"tampered": True}
    elif mutation == "gap":
        del bundle["events"][1]
    elif mutation == "model-output":
        del of_kind(bundle, "model_finished")[0]["data"]["output_snapshot"]
    else:
        target = "tool_started" if mutation == "tool-started" else "tool_result_emitted"
        bundle["events"] = [event for event in bundle["events"] if event["kind"] != target]
        renumber(bundle)
    with pytest.raises(ValueError):
        audit_trace(bundle)


@pytest.mark.asyncio
async def test_audit_rejects_raw_grounding_digest_that_disagrees_with_the_frozen_body(monkeypatch):
    _, bundle, _, _ = await autofix_turn(monkeypatch)
    of_kind(bundle, "server_autofix_read")[0]["data"]["text_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        audit_trace(bundle)


@pytest.mark.asyncio
async def test_audit_rejects_a_negative_first_elapsed_time():
    _, bundle, _ = await simple_tool_turn()
    bundle["events"][0]["elapsed_ms"] = -0.5
    with pytest.raises(ValueError):
        audit_trace(bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["content", "call-id", "tool-name", "missing"])
async def test_included_tool_observation_must_match_the_actual_frozen_model_input(mutation):
    _, bundle, _ = await simple_tool_turn()
    included = of_kind(bundle, "included_in_model_input")[0]
    if mutation == "content":
        replacement = "A different fictional tool result."
        digest = canonical_sha256(replacement)
        bundle["snapshots"][digest] = replacement
        included["data"]["content_snapshot"] = digest
    elif mutation == "call-id":
        included["data"]["tool_call_id"] = "a-different-provider-call"
    elif mutation == "tool-name":
        included["data"]["tool_name"] = "a-different-tool"
    else:
        bundle["events"].remove(included)
        renumber(bundle)
    # These are valid snapshot hashes; identity/content must match the messages.
    with pytest.raises(ValueError):
        audit_trace(bundle)


@pytest.mark.asyncio
async def test_model_input_and_inclusion_cannot_both_replace_the_actual_tool_result():
    _, bundle, _ = await simple_tool_turn()
    included = of_kind(bundle, "included_in_model_input")[0]["data"]
    started = of_kind(bundle, "model_started")[1]["data"]
    messages = copy.deepcopy(bundle["snapshots"][started["input_snapshot"]])
    replacement = "A fictional value that was never returned by the tool."
    for message in messages:
        if message.get("type") == "tool":
            message["content"] = replacement
    input_digest = canonical_sha256(messages)
    bundle["snapshots"][input_digest] = messages
    started["input_snapshot"] = input_digest
    content_digest = canonical_sha256(replacement)
    bundle["snapshots"][content_digest] = replacement
    included["content_snapshot"] = content_digest
    # The two projections agree with each other but not the prior real result.
    with pytest.raises(ValueError):
        audit_trace(bundle)


@pytest.mark.asyncio
async def test_final_answer_cannot_replace_the_observed_model_output_with_valid_new_hashes():
    _, bundle, _ = await simple_tool_turn()
    final = of_kind(bundle, "turn_final_answer")[0]["data"]
    answer = copy.deepcopy(bundle["snapshots"][final["answer_snapshot"]])
    answer["text"] = "A fictional answer not produced by any model invocation."
    digest = canonical_sha256(answer)
    bundle["snapshots"][digest] = answer
    final["answer_snapshot"] = digest
    # Both texts have no citation markers; final_cited remains internally valid.
    with pytest.raises(ValueError):
        audit_trace(bundle)

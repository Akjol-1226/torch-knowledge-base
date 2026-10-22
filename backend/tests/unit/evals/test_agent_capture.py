import asyncio
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from app.modules.chat.native_runtime import NativeReactRuntime
from app.modules.chat.observation import TurnObservation
from app.modules.chat.tools import current_kbs, get_store, use_read_view
from evals.agent import assess, audit
from evals.agent_artifacts import EvaluationPlan, load_json, load_jsonl, write_jsonl
from evals.agent_capture import capture_case, main
from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentDataset,
    canonical_sha256,
    case_sha256,
)
from evals.agent_trace import audit_trace


def _data():
    dataset = AgentDataset.model_validate({
        "dataset_schema_version": 2, "dataset_id": "synthetic", "annotation_version": "v1",
        "cases": [{
            "id": "missing", "document_group_id": "synthetic-family", "gold_status": "approved",
            "input": {"messages": [{"role": "user", "content": "Missing synthetic data?"}],
                      "kbs": ["synthetic-only"]},
            "expected_behavior": {"allowed_modes": ["refuse"]},
        }],
    })
    plan = EvaluationPlan(
        experiment_id="example", dataset_sha256=canonical_sha256(dataset), arm_ids=["baseline"],
        case_ids=["missing"], repeats=1,
    )
    return dataset, plan


class Model:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = 0
        self.scope = None

    def bind_tools(self, tools):
        return self

    async def astream(self, messages, config=None):
        self.calls += 1
        self.scope = current_kbs.get()
        yield AIMessageChunk(content="资料不足，无法确认。")
        if self.failure == "error":
            raise RuntimeError("PRIVATE_PROVIDER_CANARY")
        if self.failure == "wait":
            await asyncio.sleep(60)


async def _capture(tmp_path, *, model=None, observation=None, timeout_seconds=2):
    dataset, plan = _data()
    run = await capture_case(
        dataset, plan, case_id="missing", arm_id="baseline", repeat_index=1,
        runtime=NativeReactRuntime(model or Model(), [], "synthetic system"),
        output=tmp_path / "capture", timeout_seconds=timeout_seconds, observation=observation,
    )
    return run, dataset, plan


@pytest.mark.asyncio
async def test_capture_to_content_bound_review_to_independently_audited_report(tmp_path):
    model = Model()
    run, dataset, plan = await _capture(tmp_path, model=model)
    assert run.execution_status == "completed" and run.artifact_status == "complete"
    assert model.calls == 1 and model.scope == ["synthetic-only"]
    assert current_kbs.get() is None
    private = tmp_path / "capture" / "private"
    trace = load_json(private / "trace.json")
    assert audit_trace(trace)["model_calls"] == 1
    assert load_json(private / "manifest.json")["cost"] is None
    review = {
        "run_id": run.run_id, "case_id": run.case_id, "dataset_sha256": plan.dataset_sha256,
        "case_sha256": case_sha256(dataset.cases[0]), "answer_sha256": run.answer_sha256,
        "reviewer": {"reviewer_id": "synthetic-reviewer",
                     "protocol_version": SCORING_PROTOCOL_VERSION},
        "observed_response": "refuse", "behavior_status": "pass", "behavior_reason": "fixture",
        "fact_reviews": [], "claim_inventory_status": "complete", "claim_inventory_reason": "none",
        "claim_reviews": [],
    }
    write_jsonl(private / "reviews.jsonl", [review])
    summary = assess(private / "dataset.json", private / "plan.json", private / "runs.jsonl",
                     private / "reviews.jsonl", tmp_path / "assessment")
    assert summary["decision"] == "accept_offline_assessment"
    assert audit(tmp_path / "assessment") == summary


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "wait"])
async def test_partial_error_or_timeout_is_preserved_and_not_completed(tmp_path, failure):
    run, _, _ = await _capture(tmp_path, model=Model(failure), timeout_seconds=0.05)
    assert run.execution_status == ("error" if failure == "error" else "timeout")
    assert run.output_kind == "partial" and run.answer == "资料不足，无法确认。"
    if failure == "wait":
        assert run.artifact_status == "incomplete"
    private = tmp_path / "capture" / "private"
    assert load_jsonl(private / "runs.jsonl")[0]["answer"] == run.answer
    assert "PRIVATE_PROVIDER_CANARY" not in (private / "trace.json").read_text(encoding="utf-8")
    assert current_kbs.get() is None


@pytest.mark.asyncio
async def test_caller_cancellation_is_saved_then_propagated(tmp_path):
    task = asyncio.create_task(_capture(tmp_path, model=Model("wait")))
    for _ in range(100):
        if (tmp_path / "capture" / "private").is_dir():
            await asyncio.sleep(0.02)
            break
        await asyncio.sleep(0.002)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = load_jsonl(tmp_path / "capture" / "private" / "runs.jsonl")[0]
    assert saved["execution_status"] == "cancelled"
    assert saved["artifact_status"] == "incomplete"


@pytest.mark.asyncio
async def test_existing_output_is_rejected_before_calling_model(tmp_path):
    (tmp_path / "capture").mkdir()
    model = Model()
    with pytest.raises(FileExistsError):
        await _capture(tmp_path, model=model)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_observer_overflow_does_not_change_answer_but_capture_is_incomplete(tmp_path):
    recorder = TurnObservation("bounded", max_events=2)
    run, _, _ = await _capture(tmp_path, observation=recorder)
    assert run.answer == "资料不足，无法确认。" and run.execution_status == "completed"
    assert run.artifact_status == "incomplete"


@pytest.mark.asyncio
async def test_explicit_reader_is_context_local_and_restores_after_error():
    first, second = object(), object()

    async def reader(value):
        with use_read_view(value):
            await asyncio.sleep(0)
            assert get_store() is value
            with use_read_view(second):
                assert get_store() is second
            assert get_store() is value

    await asyncio.gather(reader(first), reader(second))


def test_capture_cli_uses_project_adapter_and_keeps_diagnostic_output_private(
    tmp_path, monkeypatch, capsys,
):
    from app.core import config, retrieval
    from app.modules.chat import runtime
    from evals.agent_artifacts import write_json

    dataset, plan = _data()
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    write_json(tmp_path / "dataset.json", dataset.model_dump(mode="json"))
    write_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        apply_litellm_env=lambda: None, chat_tool_concurrency=2,
    ))
    monkeypatch.setattr(retrieval, "TreeStore", lambda _: object())

    def factory():
        print("PRIVATE_STDOUT_CANARY")
        print("PRIVATE_STDERR_CANARY", file=sys.stderr)
        return NativeReactRuntime(Model(), [], "synthetic system")

    monkeypatch.setattr(runtime, "build_chat_runtime", factory)
    result = main([
        "--dataset", str(tmp_path / "dataset.json"), "--plan", str(tmp_path / "plan.json"),
        "--data-dir", str(tmp_path / "fake-store"), "--output", str(tmp_path / "capture"),
        "--case-id", "missing", "--arm-id", "baseline",
    ])
    output = capsys.readouterr()
    assert result == 0
    assert output.out == "execution_status=completed artifact_status=complete\n"
    assert output.err == ""


@pytest.mark.parametrize("exception", [asyncio.CancelledError, KeyboardInterrupt])
def test_cli_cancellation_reason_is_not_printed(tmp_path, monkeypatch, capsys, exception):
    from app.core import config, retrieval
    from app.modules.chat import runtime
    from evals import agent_capture
    from evals.agent_artifacts import write_json

    dataset, plan = _data()
    write_json(tmp_path / "dataset.json", dataset.model_dump(mode="json"))
    write_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        apply_litellm_env=lambda: None, chat_tool_concurrency=2,
    ))
    monkeypatch.setattr(retrieval, "TreeStore", lambda _: object())
    monkeypatch.setattr(runtime, "build_chat_runtime", lambda: object())

    async def cancelled(*args, **kwargs):
        raise exception("PRIVATE_CANCELLATION_CANARY")

    monkeypatch.setattr(agent_capture, "capture_case", cancelled)
    code = main([
        "--dataset", str(tmp_path / "dataset.json"), "--plan", str(tmp_path / "plan.json"),
        "--data-dir", str(tmp_path / "fake-store"), "--output", str(tmp_path / "capture"),
        "--case-id", "missing", "--arm-id", "baseline",
    ])
    output = capsys.readouterr()
    assert code == 130 and output.out == "" and output.err == "capture cancelled\n"


@pytest.mark.asyncio
async def test_real_kb_tool_and_threaded_autofix_share_explicit_reader(tmp_path, monkeypatch):
    from app.modules.chat import tools as chat_tools

    class Store:
        def __init__(self):
            self.reads = []

        def read_node(self, node_id):
            assert chat_tools.get_store() is self
            self.reads.append(node_id)
            return {
                "text": "Fictional node content.", "title": "Fictional",
                "cite": {"handle": node_id, "doc_id": "doc", "doc": "Fictional",
                         "section": "section", "lines": "1-2"},
            }

    class ReadingModel(Model):
        async def astream(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                yield AIMessageChunk(content="", tool_calls=[{
                    "name": "read_node", "id": "actual-read", "args": {"node_id": "doc:1"},
                    "type": "tool_call",
                }])
            else:
                yield AIMessageChunk(content="Fictional answer.[[cite:doc:1]][[cite:doc:2]]")

    def forbidden_global():
        raise AssertionError("global production store must not be read")

    monkeypatch.setattr(chat_tools, "get_tree_store", forbidden_global)
    store, model = Store(), ReadingModel()
    dataset, plan = _data()
    run = await capture_case(
        dataset, plan, case_id="missing", arm_id="baseline", repeat_index=1,
        runtime=NativeReactRuntime(model, chat_tools.KB_TOOLS, "synthetic system"),
        output=tmp_path / "capture", store=store,
    )
    assert run.artifact_status == "complete" and model.calls == 2
    assert store.reads == ["doc:1", "doc:2"]
    trace = load_json(tmp_path / "capture" / "private" / "trace.json")
    assert audit_trace(trace)["tool_calls"] == 1
    assert any(event["kind"] == "server_autofix_read" for event in trace["events"])
    with pytest.raises(AssertionError, match="global production store"):
        chat_tools.get_store()

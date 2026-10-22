"""Fictional files and stub models only; no provider or user dataset is called."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

import evals.agent_ab as ab
from evals.agent_artifacts import file_sha256, load_json, write_json
from evals.agent_experiment import ExperimentBudget, advance_state
from evals.agent_schemas import AgentDataset, canonical_json, canonical_sha256, sha256_text
from evals.agent_snapshot import create_release

ENDPOINT = "https://example.invalid/v1"
SECRET = "FICTIONAL_SECRET_CANARY"  # noqa: S105 - intentionally fictional privacy canary


def _encoded(value):
    return (canonical_json(value) + "\n").encode()


@pytest.fixture(scope="module")
def prepared_base(tmp_path_factory):
    root = tmp_path_factory.mktemp("fictional-ab")
    staging = root / "staging"
    origin = root / "fictional-source-not-read"
    markdown = "# Fictional manual\nOnly invented material.\n"
    doc = {
        "id": "doc_fiction",
        "type": "md",
        "kb": "fictional-only",
        "path": str(origin / "md/manual.md"),
        "doc_name": "Fictional manual",
        "doc_description": "Invented test material.",
        "line_count": 3,
        "structure": [
            {
                "node_id": "0001",
                "title": "Fictional manual",
                "line_num": 1,
                "text": markdown.strip(),
                "nodes": [],
            }
        ],
    }
    inputs = {
        "workspace/doc_fiction.json": _encoded(doc),
        "catalog/document_catalog.json": _encoded(
            [
                {
                    "doc_id": doc["id"],
                    "kb": doc["kb"],
                    "doc_name": doc["doc_name"],
                    "doc_description": doc["doc_description"],
                }
            ]
        ),
        "md/manual.md": markdown.encode(),
        "domain_dict_auto.txt": b"Fictional\t10\tn\n",
    }
    for name, content in inputs.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    write_json(
        staging / "source-capture.json",
        {
            "source_capture_schema_version": 1,
            "capture_method": "windows_read_handles_deny_write_delete",
            "all_handles_held_during_read": True,
            "source_data_dir": str(origin),
            "files": {name: file_sha256(staging / name) for name in inputs},
            # This is a synthetic receipt fixture, not a claim of actual OS leases.
        },
    )
    release = root / "release"
    create_release(staging, release, retrieval_top_n=50)
    dataset = AgentDataset.model_validate(
        {
            "dataset_schema_version": 2,
            "dataset_id": "fictional-dataset",
            "annotation_version": "fictional-v1",
            "cases": [
                {
                    "id": "fictional-case",
                    "document_group_id": "fictional-family",
                    "gold_status": "approved",
                    "input": {
                        "messages": [{"role": "user", "content": "PRIVATE_QUESTION_CANARY"}],
                        "kbs": ["fictional-only"],
                    },
                    "expected_behavior": {"allowed_modes": ["refuse"]},
                }
            ],
        }
    )
    dataset_file = root / "dataset.json"
    write_json(dataset_file, dataset.model_dump(mode="json"))
    experiment = root / "experiment"
    ab.prepare_experiment(
        dataset_file,
        release,
        experiment,
        shared_config=ab.SharedConfig(
            model_route="fictional-model",
            endpoint_sha256=sha256_text(ENDPOINT),
            system_prompt="Fixed fictional system.",
            retrieval_top_n=50,
        ),
        budget=ExperimentBudget(
            model_timeout_seconds=1.0,
            run_timeout_seconds=2.0,
            max_started_runs=2,
            max_agent_rounds=3,
            max_tool_concurrency=2,
            max_output_tokens=80,
            sdk_max_retries=0,
        ),
        experiment_id="fictional-experiment",
        seed=17,
    )
    return experiment


@pytest.fixture
def experiment(tmp_path, prepared_base):
    output = tmp_path / "experiment"
    shutil.copytree(prepared_base, output)
    return output


class FictionalModel:
    model_name = "fictional-model"
    temperature = 0.0
    max_retries = 0
    max_tokens = 80
    request_timeout = 1.0

    def bind_tools(self, tools):
        return self

    async def astream(self, messages, config=None):
        yield AIMessageChunk(
            content="PRIVATE_ANSWER_CANARY",
            usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
        )


def _synthetic_capture(root, index, *, overflow=False):
    from app.modules.chat.native_runtime import NativeReactRuntime
    from app.modules.chat.observation import TurnObservation
    from app.modules.chat.tools import KB_TOOLS
    from evals.agent_capture import capture_case

    private = root / "private"
    # Trusted fixture inputs: production run/audit still independently validate the release.
    dataset = AgentDataset.model_validate(load_json(private / "dataset.json"))
    plan = ab.EvaluationPlan.model_validate(load_json(private / "plan.json"))
    contract = ab.FrozenExperimentContract.model_validate(load_json(private / "contract.json"))
    config = ab.FrozenConfig.model_validate(load_json(private / "config.json"))
    slot = ab._slots(contract)[index]
    output = ab._capture_path(root, index)
    asyncio.run(
        capture_case(
            dataset,
            plan,
            case_id=slot["case_id"],
            arm_id=slot["arm_id"],
            repeat_index=slot["repeat_index"],
            runtime=NativeReactRuntime(
                FictionalModel(),
                KB_TOOLS,
                config.system_prompt,
                max_agent_rounds=contract.budget.max_agent_rounds,
                max_tool_concurrency=contract.budget.max_tool_concurrency,
            ),
            output=output,
            observation=TurnObservation(slot["run_id"], max_events=2 if overflow else 10000),
            grounding_repair_policy=slot["policy"],
        )
    )
    write_json(
        output / "worker-receipt.json",
        {
            "run_id": slot["run_id"],
            "verified_before_and_after": True,
            "input_manifest_sha256": file_sha256(root / "private/manifest.json"),
            "files": {
                name: file_sha256(output / "private" / name)
                for name in ("dataset.json", "plan.json", "runs.jsonl", "trace.json")
            },
        },
    )


@pytest.fixture
def fake_workers(monkeypatch):
    calls = []
    monkeypatch.setattr(ab, "_environment", lambda *args: {})

    def launch(root, index, env, timeout):
        assert timeout > 0
        calls.append(index)
        _synthetic_capture(root, index)

    monkeypatch.setattr(ab, "_launch_worker", launch)
    return calls


def test_prepare_uses_new_root_and_contains_no_env_or_tests(experiment):
    _, _, contract, config = ab._load_experiment(experiment)
    assert contract.budget.max_started_runs == 2
    assert config.warmup == "none"
    assert config.model_route_version_kind == "rolling_alias"
    bundle = experiment / "private/source_bundle"
    assert (bundle / "evals/agent_ab.py").exists()
    assert (bundle / ab._PROMPT).read_text(encoding="utf-8") == config.system_prompt
    assert not list(bundle.rglob(".env"))
    assert not (bundle / "tests").exists()
    assert SECRET not in (experiment / "private/config.json").read_text(encoding="utf-8")


def test_frozen_schedule_each_primary_once_and_quality_unknown(experiment, fake_workers):
    state = ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    assert state.experiment_status == "complete"
    assert fake_workers == [0, 1]
    again = ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    assert again == state and fake_workers == [0, 1]
    audit = ab.audit_experiment(experiment)
    assert audit["planned_pair_count"] == 1 and audit["comparable_pair_count"] == 0
    assert audit["primary_delta"] is None
    assert audit["promotion_decision"] == "needs_review"
    for role in ("baseline", "candidate"):
        metrics = audit["diagnostics"][role]
        assert metrics["terminal_coverage"] == 1
        assert metrics["strict_task_success_rate_all_planned"] is None
        assert metrics["model_invoke_wall_ms"]["count"] == 1
        assert metrics["usage_observed_subtotals"]["total_tokens"] == 9
    report = canonical_json(ab.public_report(audit))
    for canary in (
        "PRIVATE_QUESTION_CANARY",
        "PRIVATE_ANSWER_CANARY",
        "fictional-case",
        "fictional-family",
        str(experiment),
        SECRET,
    ):
        assert canary not in report
    assert ab.public_report(audit)["experiment_status"] == "complete"


def test_partial_worker_files_remain_missing_without_fabricated_terminal(experiment, monkeypatch):
    monkeypatch.setattr(ab, "_environment", lambda *args: {})

    def crash(root, index, env, timeout):
        path = ab._capture_path(root, index) / "private"
        path.mkdir(parents=True)
        (path / "trace.json").write_text("partial", encoding="utf-8")

    monkeypatch.setattr(ab, "_launch_worker", crash)
    state = ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    assert state.experiment_status == "interrupted"
    assert len(state.started_run_ids) == 1 and not state.terminal_run_ids
    audit = ab.audit_experiment(experiment)
    assert audit["planned_pair_count"] == 1 and audit["unresolved_pair_count"] == 1
    assert sum(v["terminal_runs"] for v in audit["diagnostics"].values()) == 0
    assert audit["conservative_delta_bounds"] == [-1.0, 1.0]


def test_previously_started_primary_is_not_reexecuted_after_restart(experiment, fake_workers):
    _, _, contract, _ = ab._load_experiment(experiment)
    first = ab._slots(contract)[0]["run_id"]
    state = ab._state(experiment, contract)
    ab._atomic_json(
        experiment / "private/state.json", advance_state(contract, state, started_run_ids=[first])
    )
    final = ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    assert fake_workers == [1]
    assert final.experiment_status == "interrupted"
    assert final.stop_reason == "missing_terminal"
    assert len(final.started_run_ids) == 2 and len(final.terminal_run_ids) == 1


@pytest.mark.parametrize("name", ["config.json", "dataset.json", "schedule.json"])
def test_frozen_input_change_is_rejected(experiment, name):
    target = experiment / "private" / name
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="input changed"):
        ab._load_experiment(experiment)


def test_contract_runtime_budget_mismatch_is_rejected_even_if_artifact_rehashed(
    experiment, fake_workers
):
    ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    _, _, contract, _ = ab._load_experiment(experiment)
    slot = ab._slots(contract)[0]
    directory = ab._capture_path(experiment, 0)
    path = directory / "private/trace.json"
    trace = load_json(path)
    next(e for e in trace["events"] if e["kind"] == "runtime_config")["data"][
        "configured_max_output_tokens"
    ] = 999
    write_json(path, trace)
    receipt = load_json(directory / "worker-receipt.json")
    receipt["files"]["trace.json"] = file_sha256(path)
    write_json(directory / "worker-receipt.json", receipt)
    with pytest.raises(ValueError, match="runtime differs"):
        ab.audit_experiment(experiment)
    assert slot["run_id"] in ab._state(experiment, contract).terminal_run_ids


def test_aggregate_jsonl_must_match_primary_artifacts(experiment, fake_workers):
    ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    (experiment / "private/runs.jsonl").write_bytes(b"")
    with pytest.raises(ValueError, match="aggregate JSONL"):
        ab.audit_experiment(experiment)


def test_new_bundle_file_or_changed_live_auditor_is_rejected(experiment):
    bundle = experiment / "private/source_bundle"
    (bundle / "evals/new_module.py").write_text("# invented", encoding="utf-8")
    with pytest.raises(ValueError, match="source bundle changed"):
        ab._load_experiment(experiment)


def test_worker_environment_is_an_explicit_nonexporting_whitelist(experiment, monkeypatch):
    from app.core import config as config_module

    _, _, _, config = ab._load_experiment(experiment)
    settings = SimpleNamespace(
        chat_model="fictional-model",
        litellm_base_url=ENDPOINT,
        litellm_api_key=SimpleNamespace(get_secret_value=lambda: SECRET),
    )
    monkeypatch.setattr(config_module, "Settings", lambda **kwargs: settings)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "OUTSIDE_SECRET")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    env = ab._environment(Path("unused-fictional"), config, experiment / "private/source_bundle")
    assert env["LITELLM_API_KEY"] == SECRET
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["LANGCHAIN_TRACING_V2"] == "false"
    assert env["LANGSMITH_TRACING"] == "false"
    assert env["PYTHONPATH"] == str(experiment / "private/source_bundle")


def test_fresh_interpreter_imports_frozen_bundle_without_calling_provider(experiment):
    bundle = experiment / "private/source_bundle"
    import os

    allowed = (
        "SystemRoot",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "COMSPEC",
        "PATHEXT",
    )
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(
        {
            "PYTHONPATH": str(bundle),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "LITELLM_BASE_URL": ENDPOINT,
            "LITELLM_API_KEY": SECRET,
            "CHAT_MODEL": "fictional-model",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed fictional verification-only worker
        [
            sys.executable,
            "-P",
            "-m",
            "evals.agent_ab",
            "_worker",
            "--root",
            str(experiment),
            "--slot-index",
            "0",
            "--verify-only",
        ],
        cwd=experiment,
        env=env,
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"worker_finished"
    assert SECRET.encode() not in result.stdout + result.stderr
    assert not (experiment / "private/captures").exists()
    assert not list(bundle.rglob("*.pyc"))


def test_public_cli_interrupts_are_safe(monkeypatch, capsys):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE_EXCEPTION_CANARY")

    monkeypatch.setattr(ab, "audit_experiment", interrupt)
    assert ab.main(["audit", "--root", "fictional"]) == 130
    output = capsys.readouterr()
    assert "PRIVATE_EXCEPTION_CANARY" not in output.out + output.err
    assert "interrupted" in output.err


def test_completed_execution_survives_bounded_recorder_overflow(experiment, monkeypatch):
    monkeypatch.setattr(ab, "_environment", lambda *args: {})

    def overflow(root, index, env, timeout):
        _synthetic_capture(root, index, overflow=True)

    monkeypatch.setattr(ab, "_launch_worker", overflow)
    state = ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    assert state.experiment_status == "complete" and len(state.terminal_run_ids) == 2
    audit = ab.audit_experiment(experiment)
    assert audit["primary_delta"] is None and audit["unresolved_pair_count"] == 1
    for metrics in audit["diagnostics"].values():
        assert metrics["execution_status_counts"] == {"completed": 1}
        assert metrics["terminal_coverage"] == 1
        assert metrics["complete_trace_coverage"] == 0
        assert metrics["strict_task_success_rate_all_planned"] is None


def test_invalid_bound_review_is_distinct_from_missing_review(experiment, fake_workers):
    ab.run_experiment(experiment, env_file=Path("unused-fictional"))
    dataset, _, contract, _ = ab._load_experiment(experiment)
    slot = ab._slots(contract)[0]
    review = {
        "run_id": slot["run_id"],
        "case_id": slot["case_id"],
        "dataset_sha256": canonical_sha256(dataset),
        "case_sha256": contract.cases[0].case_sha256,
        "answer_sha256": "0" * 64,
        "reviewer": {
            "reviewer_id": "fictional-reviewer",
            "protocol_version": "torch-agent-score/2.0-draft",
        },
        "observed_response": "unknown",
        "behavior_status": "pending",
        "behavior_reason": "PRIVATE_REVIEW_CANARY",
        "claim_inventory_status": "pending",
        "claim_inventory_reason": "fictional incomplete review",
    }
    reviews = experiment / "fictional-reviews.jsonl"
    reviews.write_text(canonical_json(review) + "\n", encoding="utf-8")
    audit = ab.audit_experiment(experiment, reviews_path=reviews)
    assert audit["diagnostics"][slot["arm_id"]]["review_status_counts"]["invalid"] == 1
    assert audit["primary_delta"] is None
    rendered = canonical_json(ab.public_report(audit))
    assert "PRIVATE_REVIEW_CANARY" not in rendered and '"invalid":1' in rendered


def test_validation_and_parsing_use_the_same_input_buffer(experiment, monkeypatch):
    real_read = ab.read_bytes
    changed = False

    def read_then_mutate(path):
        nonlocal changed
        raw = real_read(path)
        if path == experiment / "private/config.json" and not changed:
            changed = True
            value = ab._decoded(raw)
            value["model_route"] = "different-model-after-read"
            write_json(path, value)
        return raw

    monkeypatch.setattr(ab, "read_bytes", read_then_mutate)
    _, _, _, config = ab._load_experiment(experiment)
    assert config.model_route == "fictional-model"
    with pytest.raises(ValueError, match="input changed"):
        ab._load_experiment(experiment)


@pytest.mark.parametrize("area", ["release", "app", "evals"])
def test_prepare_rejects_nested_output_before_reading_or_copying(tmp_path, monkeypatch, area):
    backend = tmp_path / "fictional-backend"
    (backend / "app").mkdir(parents=True)
    (backend / "evals").mkdir()
    release = tmp_path / "fictional-release"
    release.mkdir()
    output = (release if area == "release" else backend / area) / "new-private-output"

    def must_not_read(*args, **kwargs):
        raise AssertionError("unsafe path must be rejected before reading/copying inputs")

    monkeypatch.setattr(ab, "audit_release", must_not_read)
    monkeypatch.setattr(ab, "load_dataset", must_not_read)
    with pytest.raises(ValueError, match="overlap"):
        ab.prepare_experiment(
            tmp_path / "nonexistent-dataset.json",
            release,
            output,
            source_backend=backend,
            shared_config=ab.SharedConfig(
                model_route="fictional-model",
                endpoint_sha256=sha256_text(ENDPOINT),
                system_prompt="Fixed fictional system.",
                retrieval_top_n=50,
            ),
            budget=ExperimentBudget(
                model_timeout_seconds=1.0,
                run_timeout_seconds=2.0,
                max_started_runs=2,
                max_agent_rounds=3,
                max_tool_concurrency=2,
                max_output_tokens=80,
                sdk_max_retries=0,
            ),
            experiment_id="fictional-overlap",
        )
    assert not output.exists()

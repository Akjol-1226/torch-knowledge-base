"""Offline workflow, immutable bindings, damaged artifacts and log redaction."""

import copy
import hashlib

import pytest
from pydantic import ValidationError

from evals import agent
from evals.agent_artifacts import (
    EvaluationPlan,
    RunRecord,
    file_sha256,
    load_json,
    load_jsonl,
    safe_artifact,
    strict_json,
    write_json,
    write_jsonl,
)
from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentDataset,
    canonical_sha256,
    case_sha256,
    sha256_text,
)

CANARY = "PRIVATE_CANARY_DO_NOT_EXPORT"
TEXT = f"{CANARY}: the maximum for example part A is 245 degrees C."
ANSWER = "The maximum is 245 degrees C.[[cite:doc-demo:1]]"


def _inputs(tmp_path):
    dataset = AgentDataset.model_validate({
        "dataset_schema_version": 2, "dataset_id": "demo", "annotation_version": "v1",
        "cases": [{
            "id": "q-demo", "document_group_id": "family-demo", "gold_status": "approved",
            "input": {"messages": [{"role": "user", "content": CANARY}], "kbs": [CANARY]},
            "expected_behavior": {"allowed_modes": ["answer"]},
            "required_facts": [{
                "id": "fact-demo", "kind": "numeric_constraint", "entity": "part A",
                "attribute": "temperature", "relation": "upper_bound", "value": "245",
                "unit": "degC", "document_version": "v1", "evidence_group_ids": ["group-1"],
            }],
            "evidence_groups": [{"id": "group-1", "equivalent_evidence_ids": ["evidence-1"]}],
            "gold_evidence": [{
                "id": "evidence-1", "handle": "doc-demo:1", "document_version": "v1",
                "text": TEXT, "content_sha256": sha256_text(TEXT),
            }],
        }],
    })
    case = dataset.cases[0]
    dataset_hash = canonical_sha256(dataset)
    plan = {
        "experiment_id": "experiment-demo", "dataset_sha256": dataset_hash,
        "arm_ids": ["baseline"], "case_ids": [case.id], "repeats": 1,
    }
    run = {
        "experiment_id": "experiment-demo", "arm_id": "baseline", "case_id": case.id,
        "repeat_index": 1, "run_id": "run-demo", "case_sha256": case_sha256(case),
        "answer_sha256": sha256_text(ANSWER), "answer": ANSWER,
        "execution_status": "completed", "artifact_status": "complete", "duration_ms": 5.0,
    }
    review = {
        "run_id": "run-demo", "case_id": case.id,
        "dataset_sha256": dataset_hash, "case_sha256": case_sha256(case),
        "answer_sha256": sha256_text(ANSWER),
        "reviewer": {"reviewer_id": CANARY, "protocol_version": SCORING_PROTOCOL_VERSION},
        "observed_response": "answer", "behavior_status": "pass", "behavior_reason": CANARY,
        "fact_reviews": [{"fact_id": "fact-demo", "status": "pass", "reason": CANARY}],
        "claim_inventory_status": "complete", "claim_inventory_reason": CANARY,
        "claim_reviews": [{
            "claim_id": "claim-1", "answer_span": ANSWER,
            "cited_evidence_ids": ["evidence-1"], "support_status": "supported",
            "supporting_spans": [{"evidence_id": "evidence-1", "quote": TEXT}],
            "reason": CANARY,
        }],
    }
    files = {name: tmp_path / name for name in ("dataset.json", "plan.json", "runs.jsonl", "reviews.jsonl")}
    write_json(files["dataset.json"], dataset.model_dump(mode="json"))
    write_json(files["plan.json"], plan)
    write_jsonl(files["runs.jsonl"], [run])
    write_jsonl(files["reviews.jsonl"], [review])
    return files, run, review


def _score_args(files, output, *, reviews=True):
    args = ["score", "--dataset", str(files["dataset.json"]), "--plan", str(files["plan.json"]),
            "--runs", str(files["runs.jsonl"]), "--output", str(output)]
    if reviews:
        args += ["--reviews", str(files["reviews.jsonl"])]
    return args


def _reseal(output):
    private = output / "private"
    manifest = load_json(private / "manifest.json")
    manifest["files"] = {name: file_sha256(private / name) for name in manifest["files"]}
    write_json(private / "manifest.json", manifest)
    report = agent._report(manifest["summary"], file_sha256(private / "manifest.json"))
    (output / "public" / "report.md").write_bytes(report.encode("utf-8"))


def test_validate_score_audit_report_roundtrip_without_model_or_application_imports(tmp_path, capsys):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(["validate", "--dataset", str(files["dataset.json"])]) == 0
    assert agent.main(_score_args(files, output)) == 0
    assert agent.main(["audit", "--assessment-dir", str(output)]) == 0
    assert agent.main(["report", "--assessment-dir", str(output)]) == 0
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    report = (output / "public" / "report.md").read_bytes()
    assert b"\r\n" not in report
    assert CANARY.encode() not in report
    assert b"not certify runtime execution" in report
    assert agent.audit(output)["strict_pass"] == 1


def test_no_review_is_pending_not_nonempty_answer_success(tmp_path, capsys):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output, reviews=False)) == 3
    summary = agent.audit(output)
    assert summary["strict_pass"] == 0
    assert summary["strict_pending"] == 1
    assert summary["adjudication_coverage"] == 0
    assert CANARY not in capsys.readouterr().out


def test_missing_planned_slots_cannot_become_complete_or_pass(tmp_path):
    files, _, _ = _inputs(tmp_path)
    plan = load_json(files["plan.json"])
    plan["repeats"] = 2
    write_json(files["plan.json"], plan)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 3
    summary = agent.audit(output)
    assert summary["planned_slots"] == 2
    assert summary["missing_slots"] == 1
    assert summary["strict_verified_pass_lower_bound"] == 0.5
    assert summary["experiment_status"] == "interrupted"


def test_known_failure_is_reported_even_with_other_missing_slots(tmp_path):
    files, _, review = _inputs(tmp_path)
    review["fact_reviews"][0]["status"] = "fail"
    write_jsonl(files["reviews.jsonl"], [review])
    plan = load_json(files["plan.json"])
    plan["repeats"] = 2
    write_json(files["plan.json"], plan)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 1
    assert agent.audit(output)["decision"] == "reject"


@pytest.mark.parametrize("field", ["answer_sha256", "case_sha256"])
def test_wrong_run_content_binding_is_invalid_and_does_not_log_private_data(tmp_path, capsys, field):
    files, run, _ = _inputs(tmp_path)
    run[field] = sha256_text("changed")
    write_jsonl(files["runs.jsonl"], [run])
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 2
    assert not output.exists()
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


def test_wrong_review_run_binding_cannot_be_silently_reused(tmp_path):
    files, _, review = _inputs(tmp_path)
    review["answer_sha256"] = sha256_text("other answer")
    write_jsonl(files["reviews.jsonl"], [review])
    assert agent.main(_score_args(files, tmp_path / "assessment")) == 2


def test_duplicate_primary_slot_cannot_choose_the_latest_success(tmp_path):
    files, run, _ = _inputs(tmp_path)
    other = {**run, "run_id": "second-run"}
    write_jsonl(files["runs.jsonl"], [run, other])
    assert agent.main(_score_args(files, tmp_path / "assessment")) == 2


def test_existing_assessment_is_not_overwritten(tmp_path):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    before = (output / "private" / "manifest.json").read_bytes()
    assert agent.main(_score_args(files, output)) == 2
    assert (output / "private" / "manifest.json").read_bytes() == before


def test_audit_rejects_public_report_tampering(tmp_path):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    (output / "public" / "report.md").write_text("Everything passed " + CANARY, encoding="utf-8")
    with pytest.raises(ValueError, match="public report"):
        agent.audit(output)


def test_report_uses_the_same_verified_snapshot_without_reopening_manifest(tmp_path, monkeypatch, capsys):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    capsys.readouterr()
    expected = (output / "public" / "report.md").read_bytes().decode("utf-8")
    original = agent._audit_snapshot

    def verified_then_changed(path):
        snapshot = original(path)
        (path / "private" / "manifest.json").write_bytes(b'{"changed":true}')
        return snapshot

    monkeypatch.setattr(agent, "_audit_snapshot", verified_then_changed)
    assert agent.main(["report", "--assessment-dir", str(output)]) == 0
    assert capsys.readouterr().out == expected


def test_audit_recomputes_grade_even_after_cached_file_hash_is_updated(tmp_path):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    path = output / "private" / "grades.jsonl"
    grades = load_jsonl(path)
    grades[0]["strict_status"] = "fail"
    write_jsonl(path, grades)
    _reseal(output)
    with pytest.raises(ValueError, match="independent recomputation"):
        agent.audit(output)


@pytest.mark.parametrize("target", ["grade_bool", "grade_float", "summary_bool", "source_hash"])
def test_audit_rejects_equal_but_wrong_json_types_and_source_fingerprints(tmp_path, target):
    files, _, _ = _inputs(tmp_path)
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    private = output / "private"
    if target.startswith("grade"):
        grades = load_jsonl(private / "grades.jsonl")
        grades[0]["legacy_signals"]["required_fact_count"] = True if target == "grade_bool" else 1.0
        write_jsonl(private / "grades.jsonl", grades)
    else:
        manifest = load_json(private / "manifest.json")
        if target == "summary_bool":
            manifest["summary"]["planned_slots"] = True
        else:
            manifest["source_hashes"]["dataset"] = "not-a-sha256"
        write_json(private / "manifest.json", manifest)
    _reseal(output)
    with pytest.raises((ValueError, ValidationError)):
        agent.audit(output)


def test_source_fingerprints_describe_the_exact_single_read_buffers(tmp_path):
    files, _, _ = _inputs(tmp_path)
    expected = {name.split(".")[0]: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in files.items()}
    output = tmp_path / "assessment"
    assert agent.main(_score_args(files, output)) == 0
    assert load_json(output / "private" / "manifest.json")["source_hashes"] == expected


@pytest.mark.parametrize("text", ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
                                  '{"x":-1e999}', '{"x":1,"x":2}'])
def test_strict_json_rejects_nonfinite_overflow_and_duplicate_keys(text):
    with pytest.raises(ValueError):
        strict_json(text)


@pytest.mark.parametrize("field", ["experiment_schema_version", "primary_trial_index"])
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_plan_integer_literals_do_not_accept_coercion(tmp_path, field, value):
    files, _, _ = _inputs(tmp_path)
    payload = load_json(files["plan.json"])
    payload[field] = value
    with pytest.raises(ValidationError):
        EvaluationPlan.model_validate(payload)


@pytest.mark.parametrize("field,value", [("result_schema_version", 3.0),
                                        ("trial_index", True), ("repeat_index", True)])
def test_run_integer_fields_do_not_accept_coercion(tmp_path, field, value):
    _, run, _ = _inputs(tmp_path)
    payload = copy.deepcopy(run)
    payload[field] = value
    with pytest.raises(ValidationError):
        RunRecord.model_validate(payload)


def test_unstarted_slot_cannot_fabricate_a_terminal_record(tmp_path):
    _, run, _ = _inputs(tmp_path)
    run.update(execution_status="not_started", output_kind="partial")
    with pytest.raises(ValidationError):
        RunRecord.model_validate(run)


def test_jsonl_rejects_a_truncated_or_blank_record(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_bytes(b'{"x":1}')
    with pytest.raises(ValueError, match="unsealed"):
        load_jsonl(path)
    path.write_bytes(b'{"x":1}\n\n')
    with pytest.raises(ValueError, match="line"):
        load_jsonl(path)


def test_safe_artifact_rejects_parent_traversal(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        safe_artifact(tmp_path, "../outside")


def test_cli_does_not_initialize_application_or_model_clients(tmp_path, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("app.", "openai", "langchain", "litellm")):
            raise AssertionError("offline workflow imported a live application/model module")
        return original(name, *args, **kwargs)

    files, _, _ = _inputs(tmp_path)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert agent.main(_score_args(files, tmp_path / "assessment")) == 0

"""Offline protocol validation and human-reviewed assessment; never calls models.

Commands return 0 for verified pass, 1 for known task failures, 2 for invalid
inputs/artifacts, and 3 for pending review or incomplete execution coverage.
This first-stage CLI consumes external captures; it does not execute an Agent
or certify runtime traces, annotator identity, or production readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from evals.agent_artifacts import (
    EvaluationPlan,
    RunRecord,
    file_sha256,
    load_dataset,
    parse_jsonl,
    read_bytes,
    safe_artifact,
    strict_json,
    validate_run_set,
    write_json,
    write_jsonl,
)
from evals.agent_schemas import AgentDataset, canonical_json, canonical_sha256
from evals.scoring.agent_quality import BoundReview, CaseGrade, score_case

_PRIVATE_FILES = (
    "dataset.json", "plan.json", "runs.jsonl", "reviews.jsonl", "grades.jsonl"
)


def _implementation_sha256() -> str:
    package = Path(__file__).parent
    files = ("agent_schemas.py", "agent_artifacts.py", "agent.py", "scoring/agent_quality.py")
    return canonical_sha256({name: file_sha256(package / name) for name in files})


def _grade_inputs(
    dataset: AgentDataset,
    plan: EvaluationPlan,
    runs: list[RunRecord],
    reviews: list[BoundReview],
) -> list[CaseGrade]:
    validate_run_set(dataset, plan, runs)
    cases = {case.id: case for case in dataset.cases}
    run_by_id = {run.run_id: run for run in runs}
    review_by_id: dict[str, BoundReview] = {}
    for review in reviews:
        if review.run_id not in run_by_id or review.run_id in review_by_id:
            raise ValueError("unexpected or duplicate review")
        review_by_id[review.run_id] = review
    grades = []
    for run in runs:
        grade = score_case(
            cases[run.case_id], run.answer,
            dataset_sha256=plan.dataset_sha256, run_id=run.run_id,
            review=review_by_id.get(run.run_id),
            execution_status=run.execution_status, artifact_status=run.artifact_status,
        )
        # A review with the wrong content binding is invalid input, not a way
        # to silently substitute a different human judgement.
        if grade.review_status == "invalid":
            raise ValueError("review content binding or evidence is invalid")
        grades.append(grade)
    return grades


def _summary(
    plan: EvaluationPlan, runs: list[RunRecord], grades: list[CaseGrade]
) -> dict[str, Any]:
    planned = len(plan.slots())
    passed = sum(grade.strict_status == "pass" for grade in grades)
    failed = sum(grade.strict_status == "fail" for grade in grades)
    pending = len(grades) - passed - failed
    missing = planned - len(runs)
    all_artifacts = all(run.artifact_status == "complete" for run in runs)
    if failed:
        decision = "reject"
        code = 1
    elif missing or pending or not all_artifacts:
        decision = "needs_review"
        code = 3
    else:
        decision = "accept_offline_assessment"
        code = 0
    return {
        "scope": "offline_protocol_assessment_not_runtime_certification",
        "experiment_status": "complete" if not missing else "interrupted",
        "planned_slots": planned,
        "observed_terminal_slots": len(runs),
        "missing_slots": missing,
        "strict_pass": passed,
        "strict_fail": failed,
        "strict_pending": pending,
        "strict_verified_pass_lower_bound": passed / planned,
        "adjudication_coverage": (passed + failed) / planned,
        "review_coverage": sum(grade.review_status == "valid" for grade in grades) / planned,
        "artifacts_complete": all_artifacts and not missing,
        "decision": decision,
        "exit_code": code,
    }


def _report(summary: dict[str, Any], manifest_hash: str) -> str:
    # Build a new object from a small allowlist. No user-supplied case/arm/run
    # identifiers, answers, paths, reviewer strings, or arbitrary reasons.
    keys = (
        "experiment_status", "planned_slots", "observed_terminal_slots",
        "missing_slots", "strict_pass", "strict_fail", "strict_pending",
        "strict_verified_pass_lower_bound", "adjudication_coverage", "review_coverage",
        "artifacts_complete", "decision",
    )
    lines = [
        "# Offline Agent assessment", "",
        "Scope: supplied captures and content-bound human reviews only. This report does not "
        "certify runtime execution, trace completeness, reviewer identity, or production quality.",
        "", f"Assessment manifest SHA-256: `{manifest_hash}`",
        "", "| Metric | Value |", "|---|---:|",
    ]
    for key in keys:
        value = summary[key]
        rendered = f"{value:.6f}" if isinstance(value, float) else str(value)
        lines.append(f"| {key} | {rendered} |")
    return "\n".join(lines) + "\n"


def assess(
    dataset_path: Path, plan_path: Path, runs_path: Path,
    reviews_path: Path | None, output: Path,
) -> dict[str, Any]:
    source_data = {
        "dataset": read_bytes(dataset_path), "plan": read_bytes(plan_path),
        "runs": read_bytes(runs_path),
    }
    if reviews_path is not None:
        source_data["reviews"] = read_bytes(reviews_path)
    dataset = AgentDataset.model_validate(strict_json(source_data["dataset"].decode("utf-8")))
    plan = EvaluationPlan.model_validate(strict_json(source_data["plan"].decode("utf-8")))
    runs = [RunRecord.model_validate(row) for row in parse_jsonl(source_data["runs"])]
    reviews = (
        [BoundReview.model_validate(row) for row in parse_jsonl(source_data["reviews"])]
        if reviews_path is not None else []
    )
    grades = _grade_inputs(dataset, plan, runs, reviews)
    summary = _summary(plan, runs, grades)
    # Never overwrite an earlier execution or assessment. All supplied raw
    # content is written only below private/, not to the report.
    output.mkdir(parents=True, exist_ok=False)
    private = output / "private"
    private.mkdir()
    write_json(private / "dataset.json", dataset.model_dump(mode="json"))
    write_json(private / "plan.json", plan.model_dump(mode="json"))
    write_jsonl(private / "runs.jsonl", [run.model_dump(mode="json") for run in runs])
    write_jsonl(private / "reviews.jsonl", [row.model_dump(mode="json") for row in reviews])
    write_jsonl(private / "grades.jsonl", [grade.model_dump(mode="json") for grade in grades])
    manifest = {
        "assessment_schema_version": 1,
        "implementation_sha256": _implementation_sha256(),
        "dataset_sha256": plan.dataset_sha256,
        "source_hashes": {name: hashlib.sha256(data).hexdigest()
                          for name, data in source_data.items()},
        "files": {name: file_sha256(private / name) for name in _PRIVATE_FILES},
        "summary": summary,
    }
    # Final manifest is written last; a partial bundle has no valid seal.
    write_json(private / "manifest.json", manifest)
    public = output / "public"
    public.mkdir()
    report = _report(summary, file_sha256(private / "manifest.json"))
    (public / "report.md").write_bytes(report.encode("utf-8"))
    return summary


def _audit_snapshot(output: Path) -> tuple[dict[str, Any], str]:
    private = output / "private"
    if not private.resolve(strict=True).is_relative_to(output.resolve(strict=True)):
        raise ValueError("private artifacts escape assessment root")
    manifest_bytes = read_bytes(safe_artifact(private, "manifest.json"))
    manifest = strict_json(manifest_bytes.decode("utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {
        "assessment_schema_version", "implementation_sha256", "dataset_sha256",
        "source_hashes", "files", "summary",
    }:
        raise ValueError("invalid assessment manifest")
    if type(manifest["assessment_schema_version"]) is not int or (
        manifest["assessment_schema_version"] != 1
    ):
        raise ValueError("unsupported assessment schema")
    if manifest["implementation_sha256"] != _implementation_sha256():
        raise ValueError("assessment implementation differs; create a new score set")
    sources = manifest["source_hashes"]
    if not isinstance(sources, dict) or set(sources) not in (
        {"dataset", "plan", "runs"}, {"dataset", "plan", "runs", "reviews"},
    ):
        raise ValueError("invalid source fingerprint set")
    if any(not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
           for value in sources.values()):
        raise ValueError("invalid source fingerprint")
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != set(_PRIVATE_FILES):
        raise ValueError("invalid assessment file set")
    payloads = {name: read_bytes(safe_artifact(private, name)) for name in _PRIVATE_FILES}
    for name, expected in files.items():
        if hashlib.sha256(payloads[name]).hexdigest() != expected:
            raise ValueError("assessment content hash mismatch")
    dataset = AgentDataset.model_validate(strict_json(payloads["dataset.json"].decode("utf-8")))
    plan = EvaluationPlan.model_validate(strict_json(payloads["plan.json"].decode("utf-8")))
    if manifest["dataset_sha256"] != plan.dataset_sha256:
        raise ValueError("assessment dataset binding mismatch")
    runs = [RunRecord.model_validate(row) for row in parse_jsonl(payloads["runs.jsonl"])]
    reviews = [BoundReview.model_validate(row) for row in parse_jsonl(payloads["reviews.jsonl"])]
    grades = _grade_inputs(dataset, plan, runs, reviews)
    saved_grades = parse_jsonl(payloads["grades.jsonl"])
    for row in saved_grades:
        CaseGrade.model_validate(row)
    if canonical_json([grade.model_dump(mode="json") for grade in grades]) != canonical_json(
        saved_grades
    ):
        raise ValueError("cached grades differ from independent recomputation")
    summary = _summary(plan, runs, grades)
    if canonical_json(summary) != canonical_json(manifest["summary"]):
        raise ValueError("cached summary differs from independent recomputation")
    report_bytes = read_bytes(safe_artifact(output, "public/report.md"))
    expected_report = _report(summary, hashlib.sha256(manifest_bytes).hexdigest())
    if report_bytes != expected_report.encode("utf-8"):
        raise ValueError("public report differs from audited assessment")
    return summary, expected_report


def audit(output: Path) -> dict[str, Any]:
    """Verify one immutable read snapshot; never initialize application clients."""
    return _audit_snapshot(output)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a dataset; no model calls")
    validate.add_argument("--dataset", type=Path, required=True)
    score = commands.add_parser("score", help="Assess supplied captures; no model calls")
    for name in ("dataset", "plan", "runs", "output"):
        score.add_argument(f"--{name}", type=Path, required=True)
    score.add_argument("--reviews", type=Path)
    for name in ("audit", "report"):
        command = commands.add_parser(name, help="Verify an existing assessment offline")
        command.add_argument("--assessment-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            dataset = load_dataset(args.dataset)
            print(json.dumps({
                "valid": True, "case_count": len(dataset.cases),
                "dataset_sha256": canonical_sha256(dataset),
            }))
            return 0
        if args.command == "score":
            summary = assess(args.dataset, args.plan, args.runs, args.reviews, args.output)
        else:
            summary, verified_report = _audit_snapshot(args.assessment_dir)
            if args.command == "report":
                sys.stdout.write(verified_report)
                return int(summary["exit_code"])
        print(json.dumps(summary, sort_keys=True))
        return int(summary["exit_code"])
    except (OSError, ValueError, RecursionError):
        # Validation errors may contain the complete proprietary answer/input.
        # Only a stable category is suitable for terminal/CI logs.
        print("assessment input or artifact validation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

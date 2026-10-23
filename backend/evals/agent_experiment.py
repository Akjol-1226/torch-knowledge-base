"""Frozen two-arm schedules and conservative, paired offline statistics.

No workers, model calls or file writes live here. Bound grades are inputs from
the separately audited scorer, not independent semantic judgments. Local hashes
bind a supplied contract; they cannot authenticate an entirely replaced record.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictInt, model_validator

from evals.agent_artifacts import EvaluationPlan, RunRecord, validate_run_set
from evals.agent_schemas import (
    AgentDataset,
    GoldStatus,
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    case_sha256,
)
from evals.scoring.agent_quality import CaseGrade


def _array(value: Any) -> Any:
    return tuple(value) if type(value) is list else value


def _version(value: Any) -> Any:
    if type(value) is not int:
        raise ValueError("version and primary trial must be exact integers")
    return value


VersionOne = Annotated[Literal[1], BeforeValidator(_version)]
ArmRole = Literal["baseline", "candidate"]
RepairPolicy = Literal["autofix_then_rewrite", "rewrite_only"]
ExperimentStatus = Literal["prepared", "running", "complete", "interrupted", "invalid"]
StopReason = Literal["budget_exhausted", "caller_interrupted", "worker_error", "missing_terminal"]


class FrozenModel(StrictModel):
    model_config = ConfigDict(frozen=True)


class ExperimentArm(FrozenModel):
    arm_id: Identifier
    role: ArmRole
    grounding_repair_policy: RepairPolicy

    @model_validator(mode="after")
    def fixed_policy(self) -> ExperimentArm:
        expected = "autofix_then_rewrite" if self.role == "baseline" else "rewrite_only"
        if self.grounding_repair_policy != expected:
            raise ValueError("the only arm difference is the predeclared repair policy")
        return self


class ExperimentBudget(FrozenModel):
    model_timeout_seconds: float = Field(gt=0)
    run_timeout_seconds: float = Field(gt=0)
    max_started_runs: StrictInt = Field(ge=1)
    max_agent_rounds: StrictInt = Field(ge=1)
    max_tool_concurrency: StrictInt = Field(ge=1)
    max_output_tokens: StrictInt = Field(ge=1)
    sdk_max_retries: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def timeouts(self) -> ExperimentBudget:
        if self.model_timeout_seconds > self.run_timeout_seconds:
            raise ValueError("a model timeout cannot exceed the enclosing run timeout")
        return self


class ExperimentCase(FrozenModel):
    case_id: Identifier
    case_sha256: Sha256
    document_group_id: Identifier
    gold_status: GoldStatus


class FrozenExperimentContract(FrozenModel):
    experiment_contract_version: VersionOne = 1
    experiment_id: Identifier
    dataset_sha256: Sha256
    evaluation_plan_sha256: Sha256
    snapshot_sha256: Sha256
    shared_config_sha256: Sha256
    arms: Annotated[tuple[ExperimentArm, ...], BeforeValidator(_array)]
    cases: Annotated[tuple[ExperimentCase, ...], BeforeValidator(_array)]
    repeats: StrictInt = Field(ge=1, le=100)
    seed: StrictInt = Field(ge=0, le=2**63 - 1)
    budget: ExperimentBudget
    primary_trial_index: VersionOne = 1
    schedule_policy: Literal["sha256_balanced_ab_ba/1"] = "sha256_balanced_ab_ba/1"
    primary_endpoint: Literal["strict_task_success"] = "strict_task_success"
    aggregation: Literal["case_equal_after_repeat_mean"] = "case_equal_after_repeat_mean"
    cluster_unit: Literal["document_group"] = "document_group"
    bootstrap_method: Literal["document_cluster_resample_case_equal/1"] = (
        "document_cluster_resample_case_equal/1"
    )
    quantile_method: Literal["linear_interpolation/1"] = "linear_interpolation/1"
    bootstrap_replicates: StrictInt = Field(default=2000, ge=100, le=10_000)
    interval_level: float = Field(default=0.95, ge=0.95, le=0.95)

    @model_validator(mode="after")
    def unique_frozen_bindings(self) -> FrozenExperimentContract:
        if len(self.arms) != 2 or tuple(arm.role for arm in self.arms) != (
            "baseline", "candidate"
        ) or len({arm.arm_id for arm in self.arms}) != 2:
            raise ValueError("exactly baseline and candidate must appear in that order")
        if not self.cases or len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case bindings must be nonempty and unique")
        expected_plan = EvaluationPlan(
            experiment_id=self.experiment_id, dataset_sha256=self.dataset_sha256,
            arm_ids=[arm.arm_id for arm in self.arms],
            case_ids=[case.case_id for case in self.cases], repeats=self.repeats,
        )
        if canonical_sha256(expected_plan) != self.evaluation_plan_sha256:
            raise ValueError("frozen cases/arms differ from the evaluation plan")
        return self


def freeze_experiment(
    dataset: AgentDataset, plan: EvaluationPlan, *, snapshot_sha256: str,
    shared_config_sha256: str, budget: ExperimentBudget, seed: int,
    bootstrap_replicates: int = 2000,
) -> FrozenExperimentContract:
    """Freeze both arms against the same dataset, release and configuration.

    Shared configuration and release hashes are verified by the runner's
    separate snapshot contract; these hashes are not evidence of enforcement.
    """
    dataset = AgentDataset.model_validate(dataset.model_dump(mode="python"))
    plan = EvaluationPlan.model_validate(plan.model_dump(mode="python"))
    budget = ExperimentBudget.model_validate(budget.model_dump(mode="python"))
    validate_run_set(dataset, plan, [])
    if len(plan.arm_ids) != 2:
        raise ValueError("the paired experiment requires exactly two ordered arms")
    by_id = {case.id: case for case in dataset.cases}
    return FrozenExperimentContract(
        experiment_id=plan.experiment_id, dataset_sha256=plan.dataset_sha256,
        evaluation_plan_sha256=canonical_sha256(plan), snapshot_sha256=snapshot_sha256,
        shared_config_sha256=shared_config_sha256, budget=budget, seed=seed,
        bootstrap_replicates=bootstrap_replicates,
        arms=(
            ExperimentArm(arm_id=plan.arm_ids[0], role="baseline",
                          grounding_repair_policy="autofix_then_rewrite"),
            ExperimentArm(arm_id=plan.arm_ids[1], role="candidate",
                          grounding_repair_policy="rewrite_only"),
        ),
        cases=tuple(ExperimentCase(
            case_id=identity, case_sha256=case_sha256(by_id[identity]),
            document_group_id=by_id[identity].document_group_id,
            gold_status=by_id[identity].gold_status,
        ) for identity in plan.case_ids), repeats=plan.repeats,
    )


def _validated(contract: FrozenExperimentContract) -> FrozenExperimentContract:
    # Revalidate even a model_copy/model_construct supplied by another caller.
    # JSON-mode serializers may coerce a forged bool in an integer field to 1.
    return FrozenExperimentContract.model_validate(contract.model_dump(mode="python"))


def primary_run_id(
    contract: FrozenExperimentContract, arm_id: str, case_id: str, repeat_index: int,
) -> str:
    contract = _validated(contract)
    if (
        arm_id not in {arm.arm_id for arm in contract.arms}
        or case_id not in {case.case_id for case in contract.cases}
        or type(repeat_index) is not int or not 1 <= repeat_index <= contract.repeats
    ):
        raise ValueError("primary run is outside the frozen schedule")
    return _primary_id(canonical_sha256(contract), arm_id, case_id, repeat_index)


def _primary_id(contract_sha256: str, arm_id: str, case_id: str, repeat_index: int) -> str:
    return "primary-" + canonical_sha256({
        "contract_sha256": contract_sha256, "arm_id": arm_id,
        "case_id": case_id, "repeat_index": repeat_index, "trial_index": 1,
    })


class ScheduledPair(FrozenModel):
    pair_id: Identifier
    case_id: Identifier
    repeat_index: StrictInt = Field(ge=1)
    arm_order: Annotated[tuple[Identifier, Identifier], BeforeValidator(_array)]
    baseline_run_id: Identifier
    candidate_run_id: Identifier


def build_schedule(contract: FrozenExperimentContract) -> tuple[ScheduledPair, ...]:
    """Deterministic balanced AB/BA order, independent of Python RNG versions."""
    contract = _validated(contract)
    keys = [(case.case_id, repeat) for case in contract.cases
            for repeat in range(1, contract.repeats + 1)]
    ranked = sorted(keys, key=lambda key: canonical_sha256({
        "seed": contract.seed, "case_id": key[0], "repeat_index": key[1],
    }))
    offset = int(canonical_sha256({"seed": contract.seed})[-1], 16) % 2
    orientation = {key: (rank + offset) % 2 for rank, key in enumerate(ranked)}
    baseline, candidate = (arm.arm_id for arm in contract.arms)
    contract_hash = canonical_sha256(contract)
    return tuple(ScheduledPair(
        pair_id="pair-" + canonical_sha256({
            "contract_sha256": contract_hash, "case_id": case,
            "repeat_index": repeat,
        }), case_id=case, repeat_index=repeat,
        arm_order=(baseline, candidate) if orientation[(case, repeat)] == 0 else (
            candidate, baseline
        ), baseline_run_id=_primary_id(contract_hash, baseline, case, repeat),
        candidate_run_id=_primary_id(contract_hash, candidate, case, repeat),
    ) for case, repeat in keys)


class ExperimentState(FrozenModel):
    state_schema_version: VersionOne = 1
    contract_sha256: Sha256
    revision: StrictInt = Field(default=0, ge=0)
    experiment_status: ExperimentStatus = "prepared"
    started_run_ids: Annotated[tuple[Identifier, ...], BeforeValidator(_array)] = ()
    terminal_run_ids: Annotated[tuple[Identifier, ...], BeforeValidator(_array)] = ()
    stop_reason: StopReason | None = None

    @model_validator(mode="after")
    def consistent_state(self) -> ExperimentState:
        for identities in (self.started_run_ids, self.terminal_run_ids):
            if len(identities) != len(set(identities)):
                raise ValueError("state contains duplicate run identities")
        if not set(self.terminal_run_ids) <= set(self.started_run_ids):
            raise ValueError("a terminal run must have been started")
        if (self.experiment_status == "interrupted") != (self.stop_reason is not None):
            raise ValueError("interrupted state requires an explicit stop reason")
        if self.experiment_status == "prepared" and self.started_run_ids:
            raise ValueError("prepared state cannot already contain executions")
        return self


def initial_state(contract: FrozenExperimentContract) -> ExperimentState:
    return ExperimentState(contract_sha256=canonical_sha256(_validated(contract)))


def advance_state(
    contract: FrozenExperimentContract, state: ExperimentState, *,
    started_run_ids: Sequence[str] = (), terminal_run_ids: Sequence[str] = (),
    finalize: bool = False, stop_reason: StopReason | None = None,
) -> ExperimentState:
    """Append observed boundaries. A terminal state cannot be reopened for reruns."""
    contract = _validated(contract)
    state = ExperimentState.model_validate(state.model_dump(mode="python"))
    if type(finalize) is not bool:
        raise ValueError("finalize must be a boolean")
    if state.contract_sha256 != canonical_sha256(contract):
        raise ValueError("state belongs to another frozen contract")
    if state.experiment_status in {"complete", "interrupted", "invalid"}:
        raise ValueError("a finalized state cannot be reopened")
    scheduled = build_schedule(contract)
    planned = {identity for pair in scheduled for identity in (
        pair.baseline_run_id, pair.candidate_run_id
    )}
    started = set(state.started_run_ids) | set(started_run_ids)
    terminal = set(state.terminal_run_ids) | set(terminal_run_ids)
    if not started <= planned or not terminal <= started:
        raise ValueError("state contains unexpected or unstarted run identities")
    if len(started) > contract.budget.max_started_runs:
        raise ValueError("started run budget exceeded")
    if stop_reason is not None and not finalize:
        raise ValueError("a stop reason must finalize the experiment")
    status: ExperimentStatus = "running" if started else "prepared"
    if finalize:
        status = "complete" if terminal == planned else "interrupted"
        if status == "complete" and stop_reason is not None:
            raise ValueError("a fully observed experiment is complete, not interrupted")
        if status == "interrupted" and stop_reason is None:
            stop_reason = "missing_terminal"
    return ExperimentState(
        contract_sha256=state.contract_sha256, revision=state.revision + 1,
        experiment_status=status, started_run_ids=tuple(sorted(started)),
        terminal_run_ids=tuple(sorted(terminal)), stop_reason=stop_reason,
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _validate_observations(
    contract: FrozenExperimentContract, runs: Sequence[RunRecord], grades: Sequence[CaseGrade],
) -> tuple[dict[str, RunRecord], dict[str, CaseGrade]]:
    cases = {case.case_id: case for case in contract.cases}
    by_run: dict[str, RunRecord] = {}
    for supplied in runs:
        run = RunRecord.model_validate(supplied.model_dump(mode="python"))
        if run.run_id in by_run or run.experiment_id != contract.experiment_id:
            raise ValueError("duplicate run or wrong experiment")
        if run.run_id != primary_run_id(contract, *run.slot()):
            raise ValueError("only the predeclared primary run may enter paired statistics")
        if run.case_sha256 != cases[run.case_id].case_sha256:
            raise ValueError("run differs from the frozen case")
        by_run[run.run_id] = run
    if len(by_run) > contract.budget.max_started_runs:
        raise ValueError("observed runs exceed the frozen started-run budget")
    by_grade: dict[str, CaseGrade] = {}
    for supplied in grades:
        grade = CaseGrade.model_validate(supplied.model_dump(mode="python"))
        run = by_run.get(grade.run_id)
        if run is None or grade.run_id in by_grade:
            raise ValueError("grade is duplicate or has no primary execution")
        if (
            grade.dataset_sha256 != contract.dataset_sha256 or grade.case_id != run.case_id
            or grade.case_sha256 != run.case_sha256 or grade.answer_sha256 != run.answer_sha256
            or grade.execution_status != run.execution_status
            or grade.artifact_status != run.artifact_status
        ):
            raise ValueError("grade binding differs from the primary run")
        if grade.strict_status == "pass" and (
            run.execution_status != "completed" or run.artifact_status != "complete"
            or grade.review_status != "valid" or cases[run.case_id].gold_status != "approved"
            or not run.answer.strip()
            or set(grade.dimensions) != {
                "behavior", "required_facts", "claim_inventory", "claim_support"
            }
            or any(item.status not in {"pass", "not_applicable"}
                   for item in grade.dimensions.values())
            or grade.dimensions["behavior"].status != "pass"
            or grade.dimensions["claim_inventory"].status != "pass"
        ):
            raise ValueError("a pending, incomplete or unreviewed execution cannot be a success")
        by_grade[grade.run_id] = grade
    return by_run, by_grade


def _outcome(run: RunRecord | None, grade: CaseGrade | None) -> tuple[float | None, str]:
    if run is None:
        return None, "missing_run"
    if run.execution_status != "completed":
        return 0.0, "execution_contract_failed"
    if not run.answer.strip():
        return 0.0, "empty_response"
    if grade is None:
        return None, "missing_grade"
    if grade.strict_status == "pending":
        return None, "pending_grade"
    return (1.0, "strict_pass") if grade.strict_status == "pass" else (0.0, "strict_fail")


def paired_statistics(
    contract: FrozenExperimentContract, runs: Sequence[RunRecord], grades: Sequence[CaseGrade],
) -> dict[str, Any]:
    """Case-macro estimate, conditional subset and worst-case missing bounds.

    Missing outcomes span [0,1]. A run error is known zero without a semantic
    review. No best/latest rerun selection or automatic promotion is performed.
    """
    contract = _validated(contract)
    by_run, by_grade = _validate_observations(contract, runs, grades)
    per_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reasons: Counter[str] = Counter()
    for pair in build_schedule(contract):
        outcomes = []
        for identity in (pair.baseline_run_id, pair.candidate_run_id):
            outcome, reason = _outcome(by_run.get(identity), by_grade.get(identity))
            outcomes.append(outcome)
            reasons[reason] += 1
        baseline, candidate = outcomes
        low = (candidate if candidate is not None else 0) - (
            baseline if baseline is not None else 1
        )
        high = (candidate if candidate is not None else 1) - (
            baseline if baseline is not None else 0
        )
        per_case[pair.case_id].append({
            "pair_id": pair.pair_id, "repeat_index": pair.repeat_index,
            "baseline": baseline, "candidate": candidate,
            "delta": candidate - baseline if baseline is not None and candidate is not None
            else None, "lower_bound": low, "upper_bound": high,
        })
    rows = []
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in contract.cases:
        pairs = per_case[case.case_id]
        comparable = [pair["delta"] for pair in pairs if pair["delta"] is not None]
        row = {
            "case_id": case.case_id, "document_group_id": case.document_group_id,
            "planned_pair_count": contract.repeats, "comparable_pair_count": len(comparable),
            "conditional_delta": _mean(comparable),
            "delta": _mean(comparable) if len(comparable) == contract.repeats else None,
            "lower_bound": _mean([pair["lower_bound"] for pair in pairs]),
            "upper_bound": _mean([pair["upper_bound"] for pair in pairs]), "pairs": pairs,
        }
        rows.append(row)
        clusters[case.document_group_id].append(row)
    comparable_count = sum(row["comparable_pair_count"] for row in rows)
    planned_count = len(rows) * contract.repeats
    all_decided = comparable_count == planned_count
    conditional = [row["conditional_delta"] for row in rows
                   if row["conditional_delta"] is not None]
    point = _mean([row["delta"] for row in rows]) if all_decided else None
    interval = None
    interval_reason = "incomplete_primary_outcomes" if not all_decided else (
        "single_document_group" if len(clusters) == 1 else None
    )
    if interval_reason is None:
        groups = sorted(clusters)
        samples = []
        for replicate in range(contract.bootstrap_replicates):
            values = []
            for draw in range(len(groups)):
                index = int(canonical_sha256({
                    "method": "document_cluster_bootstrap/1", "seed": contract.seed,
                    "replicate": replicate, "draw": draw,
                }), 16) % len(groups)
                values.extend(row["delta"] for row in clusters[groups[index]])
            samples.append(sum(values) / len(values))
        interval = [_percentile(samples, 0.025), _percentile(samples, 0.975)]
    return {
        "statistics_schema_version": 1, "contract_sha256": canonical_sha256(contract),
        "primary_endpoint": contract.primary_endpoint, "aggregation": contract.aggregation,
        "bootstrap_method": contract.bootstrap_method, "quantile_method": contract.quantile_method,
        "bootstrap_replicates": contract.bootstrap_replicates,
        "planned_pair_count": planned_count, "comparable_pair_count": comparable_count,
        "unresolved_pair_count": planned_count - comparable_count,
        "pair_decision_coverage": comparable_count / planned_count,
        "planned_case_count": len(rows), "conditional_case_count": len(conditional),
        "document_group_count": len(clusters), "outcome_counts": dict(sorted(reasons.items())),
        "primary_delta": point, "conditional_case_macro_delta": _mean(conditional),
        "conservative_delta_bounds": [
            _mean([row["lower_bound"] for row in rows]),
            _mean([row["upper_bound"] for row in rows]),
        ],
        "document_cluster_bootstrap_interval": interval,
        "interval_level": contract.interval_level, "interval_unavailable_reason": interval_reason,
        "few_document_groups": len(clusters) < 5,
        "promotion_decision": "needs_review" if not all_decided else "not_evaluated",
        "scope": "paired_primary_endpoint_not_full_guardrail_or_promotion_decision",
        "cases": rows,
        "document_groups": [{
            "document_group_id": group, "case_count": len(items),
            "delta": _mean([row["delta"] for row in items])
            if all(row["delta"] is not None for row in items) else None,
        } for group, items in sorted(clusters.items())],
    }

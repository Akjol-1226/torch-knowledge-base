"""Purely fictional two-arm contracts; no workers, models or business records."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from evals.agent_artifacts import EvaluationPlan, RunRecord
from evals.agent_experiment import (
    ExperimentArm,
    ExperimentBudget,
    FrozenExperimentContract,
    advance_state,
    build_schedule,
    freeze_experiment,
    initial_state,
    paired_statistics,
    primary_run_id,
)
from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentDataset,
    canonical_sha256,
    case_sha256,
    sha256_text,
)
from evals.scoring.agent_quality import BoundReview, CaseGrade, score_case


def fixture(groups=("fictional-family",), repeats=1, *, seed=7, max_started_runs=None):
    dataset = AgentDataset.model_validate({
        "dataset_schema_version": 2, "dataset_id": "fictional", "annotation_version": "v1",
        "cases": [{
            "id": f"q{index}", "document_group_id": group, "gold_status": "approved",
            "input": {"messages": [{"role": "user", "content": f"Fictional question {index}?"}],
                      "kbs": ["fictional-only"]},
            "expected_behavior": {"allowed_modes": ["refuse"]},
        } for index, group in enumerate(groups)],
    })
    plan = EvaluationPlan(
        experiment_id="fictional-experiment", dataset_sha256=canonical_sha256(dataset),
        arm_ids=["baseline", "candidate"], case_ids=[case.id for case in dataset.cases],
        repeats=repeats,
    )
    budget = ExperimentBudget(
        model_timeout_seconds=5, run_timeout_seconds=20,
        max_started_runs=max_started_runs or len(groups) * repeats * 2,
        max_agent_rounds=5, max_tool_concurrency=2, max_output_tokens=256, sdk_max_retries=0,
    )
    contract = freeze_experiment(
        dataset, plan, snapshot_sha256="a" * 64, shared_config_sha256="b" * 64,
        budget=budget, seed=seed, bootstrap_replicates=100,
    )
    return dataset, plan, contract


def observed(dataset, contract, case_index, arm_index, repeat, value):
    case = dataset.cases[case_index]
    arm = contract.arms[arm_index].arm_id
    execution = "error" if value == "error" else "completed"
    answer = "" if value == "empty" else "A fictional response."
    run_id = primary_run_id(contract, arm, case.id, repeat)
    run = RunRecord(
        experiment_id=contract.experiment_id, arm_id=arm, case_id=case.id,
        repeat_index=repeat, run_id=run_id, case_sha256=case_sha256(case),
        answer_sha256=sha256_text(answer), answer=answer, execution_status=execution,
        artifact_status="complete", output_kind="final" if execution == "completed" else "partial",
    )
    if value == "error":
        return run, None
    review = None
    if value in (0, 1):
        review = BoundReview.model_validate({
            "run_id": run_id, "case_id": case.id, "dataset_sha256": contract.dataset_sha256,
            "case_sha256": run.case_sha256, "answer_sha256": run.answer_sha256,
            "reviewer": {"reviewer_id": "fictional-reviewer",
                         "protocol_version": SCORING_PROTOCOL_VERSION},
            "observed_response": "refuse", "behavior_status": "pass" if value else "fail",
            "behavior_reason": "A fictional calibration judgment.",
            "claim_inventory_status": "complete", "claim_inventory_reason": "No factual claims.",
        })
    grade = score_case(
        case, answer, dataset_sha256=contract.dataset_sha256, run_id=run_id, review=review,
        execution_status=run.execution_status, artifact_status=run.artifact_status,
    )
    return run, grade


def records(dataset, contract, values):
    """values[(case_index, repeat)] = (baseline, candidate); missing omits a run."""
    runs, grades = [], []
    for (case_index, repeat), pair in values.items():
        for arm_index, value in enumerate(pair):
            if value == "missing":
                continue
            run, grade = observed(dataset, contract, case_index, arm_index, repeat, value)
            runs.append(run)
            if grade is not None:
                grades.append(grade)
    return runs, grades


def all_ids(contract):
    return tuple(identity for pair in build_schedule(contract)
                 for identity in (pair.baseline_run_id, pair.candidate_run_id))


def test_frozen_schedule_is_reproducible_balanced_and_pair_bound():
    _, _, contract = fixture(repeats=11)
    schedule = build_schedule(contract)
    assert schedule == build_schedule(contract)
    counts = [sum(pair.arm_order[0] == arm.arm_id for pair in schedule) for arm in contract.arms]
    assert abs(counts[0] - counts[1]) == 1
    assert [pair.repeat_index for pair in schedule] == list(range(1, 12))
    assert len({pair.pair_id for pair in schedule}) == 11
    assert len(set(all_ids(contract))) == 22
    for pair in schedule:
        assert pair.baseline_run_id == primary_run_id(contract, "baseline", "q0", pair.repeat_index)
        assert set(pair.arm_order) == {"baseline", "candidate"}


def test_seed_and_frozen_resource_changes_produce_new_primary_identities():
    _, _, first = fixture(repeats=20, seed=1)
    _, _, second = fixture(repeats=20, seed=2)
    assert [pair.arm_order for pair in build_schedule(first)] != [pair.arm_order for pair in build_schedule(second)]
    assert set(all_ids(first)).isdisjoint(all_ids(second))
    payload = first.model_dump(mode="json")
    payload["shared_config_sha256"] = "c" * 64
    changed = FrozenExperimentContract.model_validate(payload)
    assert set(all_ids(first)).isdisjoint(all_ids(changed))


def test_contract_roundtrips_json_without_mutable_nested_lists():
    _, _, contract = fixture()
    rebuilt = FrozenExperimentContract.model_validate(contract.model_dump(mode="json"))
    assert canonical_sha256(rebuilt) == canonical_sha256(contract)
    with pytest.raises(ValidationError):
        contract.arms[0].arm_id = "replacement"
    with pytest.raises(ValidationError):
        contract.budget.max_output_tokens = 999
    assert isinstance(contract.cases, tuple)
    assert isinstance(build_schedule(contract), tuple)


@pytest.mark.parametrize("field,value", [
    ("experiment_contract_version", True), ("primary_trial_index", True),
    ("seed", True), ("repeats", True), ("bootstrap_replicates", True),
    ("snapshot_sha256", "A" * 64), ("unknown", 1),
])
def test_contract_rejects_unsupported_or_coerced_values(field, value):
    _, _, contract = fixture()
    payload = contract.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        FrozenExperimentContract.model_validate(payload)


@pytest.mark.parametrize("mutation", ["case-id", "case-order", "arm-id", "duplicate-case", "policy"])
def test_contract_cannot_drift_from_its_plan_or_single_variable(mutation):
    _, _, contract = fixture(("family-a", "family-b"))
    payload = contract.model_dump(mode="json")
    if mutation == "case-id":
        payload["cases"][0]["case_id"] = "other"
    elif mutation == "case-order":
        payload["cases"].reverse()
    elif mutation == "arm-id":
        payload["arms"][0]["arm_id"] = "other"
    elif mutation == "duplicate-case":
        payload["cases"][1] = payload["cases"][0]
    else:
        payload["arms"][0]["grounding_repair_policy"] = "rewrite_only"
    with pytest.raises(ValidationError):
        FrozenExperimentContract.model_validate(payload)


def test_old_single_arm_plan_cannot_silently_become_a_paired_experiment():
    dataset, plan, contract = fixture()
    plan.arm_ids.pop()
    with pytest.raises(ValueError, match="two"):
        freeze_experiment(dataset, plan, snapshot_sha256="a" * 64,
                          shared_config_sha256="b" * 64, budget=contract.budget, seed=1)


@pytest.mark.parametrize("field,value", [
    ("model_timeout_seconds", 21), ("model_timeout_seconds", float("nan")),
    ("run_timeout_seconds", 0), ("max_started_runs", True), ("max_output_tokens", -1),
    ("sdk_max_retries", True),
])
def test_budget_rejects_false_measurements_or_inconsistent_deadlines(field, value):
    _, _, contract = fixture()
    payload = contract.budget.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        ExperimentBudget.model_validate(payload)


def test_only_predeclared_role_policy_mapping_is_allowed():
    with pytest.raises(ValidationError):
        ExperimentArm.model_validate({
            "arm_id": "baseline", "role": "baseline", "grounding_repair_policy": "auto",
        })


def test_experiment_lifecycle_does_not_claim_quality_or_reopen_primary_trials():
    _, _, contract = fixture()
    state = initial_state(contract)
    first = advance_state(contract, state, started_run_ids=all_ids(contract))
    assert state.experiment_status == "prepared" and not state.started_run_ids
    assert first.experiment_status == "running" and first.revision == 1
    final = advance_state(contract, first, terminal_run_ids=all_ids(contract), finalize=True)
    assert final.experiment_status == "complete"
    with pytest.raises(ValueError, match="reopened"):
        advance_state(contract, final, started_run_ids=all_ids(contract))


def test_budget_stop_retains_unstarted_slots_and_cannot_start_an_extra_run():
    _, _, contract = fixture(max_started_runs=1)
    ids = all_ids(contract)
    state = advance_state(contract, initial_state(contract), started_run_ids=ids[:1],
                          terminal_run_ids=ids[:1])
    with pytest.raises(ValueError, match="budget"):
        advance_state(contract, state, started_run_ids=ids[1:])
    stopped = advance_state(contract, state, finalize=True, stop_reason="budget_exhausted")
    assert stopped.experiment_status == "interrupted"
    assert stopped.stop_reason == "budget_exhausted"
    assert len(all_ids(contract)) == 2 and len(stopped.started_run_ids) == 1


def test_state_is_bound_to_the_frozen_contract_and_actual_starts():
    _, _, contract = fixture()
    _, _, another = fixture(seed=8)
    with pytest.raises(ValueError, match="another"):
        advance_state(another, initial_state(contract))
    with pytest.raises(ValueError, match="unstarted"):
        advance_state(contract, initial_state(contract), terminal_run_ids=all_ids(contract))
    with pytest.raises(ValueError, match="unexpected"):
        advance_state(contract, initial_state(contract), started_run_ids=["invented"])
    assert advance_state(contract, initial_state(contract), finalize=True).experiment_status == "interrupted"


def test_one_document_group_has_descriptive_delta_but_no_significance_or_promotion():
    dataset, _, contract = fixture(repeats=4)
    runs, grades = records(dataset, contract, {(0, r): (0, 1) for r in range(1, 5)})
    result = paired_statistics(contract, runs, grades)
    assert result["primary_delta"] == 1
    assert result["conservative_delta_bounds"] == [1, 1]
    assert result["document_group_count"] == 1
    assert result["document_cluster_bootstrap_interval"] is None
    assert result["interval_unavailable_reason"] == "single_document_group"
    assert result["promotion_decision"] == "not_evaluated"


@pytest.mark.parametrize("pair,expected", [
    (("missing", "missing"), [-1, 1]), ((None, 1), [0, 1]),
    ((1, None), [-1, 0]), (("error", None), [0, 1]),
])
def test_missing_and_unreviewed_outcomes_keep_full_denominator_and_sharp_bounds(pair, expected):
    dataset, _, contract = fixture()
    runs, grades = records(dataset, contract, {(0, 1): pair})
    result = paired_statistics(contract, runs, grades)
    assert result["planned_pair_count"] == result["unresolved_pair_count"] == 1
    assert result["comparable_pair_count"] == 0 and result["pair_decision_coverage"] == 0
    assert result["primary_delta"] is None
    assert result["conservative_delta_bounds"] == expected
    assert result["promotion_decision"] == "needs_review"


@pytest.mark.parametrize("baseline", ["error", "empty"])
def test_execution_failure_or_empty_response_is_known_failure_without_human_review(baseline):
    dataset, _, contract = fixture()
    runs, grades = records(dataset, contract, {(0, 1): (baseline, 1)})
    # An empty final answer is also a known contract failure without a grade.
    grades = [grade for grade in grades if grade.run_id == runs[1].run_id]
    result = paired_statistics(contract, runs, grades)
    assert result["primary_delta"] == 1 and result["comparable_pair_count"] == 1


def test_conditional_macro_averages_repeats_within_case_instead_of_pooling_lucky_pairs():
    dataset, _, contract = fixture(("family-a", "family-b"), repeats=2)
    runs, grades = records(dataset, contract, {
        (0, 1): (0, 1), (0, 2): (1, None), (1, 1): (1, 0), (1, 2): (1, 0),
    })
    result = paired_statistics(contract, runs, grades)
    assert result["planned_pair_count"] == 4 and result["comparable_pair_count"] == 3
    assert result["primary_delta"] is None
    assert result["conditional_case_macro_delta"] == 0
    assert result["conservative_delta_bounds"] == [-0.5, -0.25]
    assert result["document_cluster_bootstrap_interval"] is None


def test_point_estimate_is_case_equal_and_cluster_bootstrap_keeps_whole_document_families():
    dataset, _, contract = fixture(("small-family",) + ("large-family",) * 9)
    runs, grades = records(dataset, contract, {
        (index, 1): (0, 1) if index == 0 else (1, 1) for index in range(10)
    })
    result = paired_statistics(contract, runs, grades)
    assert result["primary_delta"] == pytest.approx(0.1)
    assert result["document_cluster_bootstrap_interval"] == [0, 1]
    assert result["few_document_groups"] is True
    assert result["promotion_decision"] == "not_evaluated"
    assert paired_statistics(contract, runs, grades) == result


@pytest.mark.parametrize("mutation", ["duplicate", "latest", "different-answer", "case", "dataset", "status"])
def test_paired_statistics_rejects_reruns_and_broken_grade_bindings(mutation):
    dataset, _, contract = fixture()
    runs, grades = records(dataset, contract, {(0, 1): (0, 1)})
    if mutation == "duplicate":
        runs.append(runs[0])
    elif mutation == "latest":
        payload = runs[0].model_dump(mode="json")
        payload["run_id"] = "a-new-diagnostic-run-id"
        runs[0] = RunRecord.model_validate(payload)
    else:
        payload = grades[0].model_dump(mode="json")
        field = {"different-answer": "answer_sha256", "case": "case_sha256",
                 "dataset": "dataset_sha256", "status": "execution_status"}[mutation]
        payload[field] = "error" if mutation == "status" else "0" * 64
        grades[0] = CaseGrade.model_validate(payload)
    with pytest.raises(ValueError):
        paired_statistics(contract, runs, grades)


@pytest.mark.parametrize("mutation", ["review", "artifact", "dimension", "gold"])
def test_a_success_label_cannot_upgrade_missing_reviews_artifacts_or_gold(mutation):
    dataset, _, contract = fixture()
    runs, grades = records(dataset, contract, {(0, 1): (1, 1)})
    if mutation == "gold":
        payload = contract.model_dump(mode="json")
        payload["cases"][0]["gold_status"] = "needs_review"
        contract = FrozenExperimentContract.model_validate(payload)
        # A changed frozen contract has different predeclared primary IDs.
    elif mutation == "artifact":
        runs[0] = runs[0].model_copy(update={"artifact_status": "incomplete"})
        grades[0] = grades[0].model_copy(update={"artifact_status": "incomplete"})
    else:
        payload = grades[0].model_dump(mode="json")
        if mutation == "review":
            payload["review_status"] = "missing"
        else:
            payload["dimensions"]["behavior"]["status"] = "pending"
        grades[0] = CaseGrade.model_validate(payload)
    with pytest.raises(ValueError):
        paired_statistics(contract, runs, grades)


def test_grade_cannot_exist_without_a_real_primary_run():
    dataset, _, contract = fixture()
    runs, grades = records(dataset, contract, {(0, 1): (1, 1)})
    with pytest.raises(ValueError, match="no primary"):
        paired_statistics(contract, runs[1:], grades)


def test_forged_model_copy_is_revalidated_before_schedule_generation():
    _, _, contract = fixture()
    forged = contract.model_copy(update={"repeats": True})
    with pytest.raises(ValidationError):
        build_schedule(forged)
    with pytest.raises(ValidationError):
        primary_run_id(forged, "baseline", "q0", 1)
    payload = copy.deepcopy(contract.model_dump(mode="json"))
    payload["cases"][0]["case_id"] = "contains whitespace"
    with pytest.raises(ValidationError):
        FrozenExperimentContract.model_validate(payload)


@pytest.mark.parametrize("target", ["dataset", "plan", "budget"])
def test_freeze_revalidates_caller_models_without_json_coercion(target):
    dataset, plan, contract = fixture()
    budget = contract.budget
    if target == "dataset":
        dataset = dataset.model_copy(update={"dataset_schema_version": True})
    elif target == "plan":
        plan = plan.model_copy(update={"repeats": True})
    else:
        budget = budget.model_copy(update={"max_output_tokens": True})
    with pytest.raises(ValidationError):
        freeze_experiment(dataset, plan, snapshot_sha256="a" * 64,
                          shared_config_sha256="b" * 64, budget=budget, seed=1)

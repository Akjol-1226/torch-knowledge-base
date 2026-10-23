"""Behavioral oracles for explicit reviews, binding failures and plan coverage."""

import copy

import pytest
from pydantic import ValidationError

from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentEvalCase,
    GoldEvidence,
    case_sha256,
    sha256_text,
)
from evals.scoring.agent_quality import (
    BoundReview,
    CaseGrade,
    ReviewerSource,
    aggregate_grades,
    score_case,
)

DATASET_HASH = sha256_text("frozen-test-dataset")
TEXT = "部件A在条件C下，最高允许温度为245℃。"
ANSWER = "部件A在条件C下最高允许温度为245℃。[[cite:doc_a:0001]]"


def _case(mode="answer", *, gold_status="approved"):
    payload = {
        "id": "q-1",
        "document_group_id": "family-a",
        "input": {"messages": [{"role": "user", "content": "最高温度是多少？"}], "kbs": ["demo"]},
        "expected_behavior": {"allowed_modes": [mode]},
        "gold_status": gold_status,
    }
    if mode == "clarify":
        payload["expected_behavior"]["missing_slots"] = ["part_number"]
    if mode == "answer":
        payload.update(
            required_facts=[
                {
                    "id": "f-1",
                    "kind": "numeric_constraint",
                    "entity": "部件A",
                    "attribute": "temperature",
                    "relation": "upper_bound",
                    "value": "245",
                    "unit": "degC",
                    "conditions": ["条件C"],
                    "document_version": "v1",
                    "evidence_group_ids": ["eg-1"],
                }
            ],
            evidence_groups=[{"id": "eg-1", "equivalent_evidence_ids": ["ev-1"]}],
            gold_evidence=[
                {
                    "id": "ev-1",
                    "handle": "doc_a:0001",
                    "document_version": "v1",
                    "text": TEXT,
                    "content_sha256": sha256_text(TEXT),
                }
            ],
        )
    return AgentEvalCase.model_validate(payload)


def _review(case, answer=ANSWER, *, run_id="run-1", **changes):
    payload = {
        "run_id": run_id,
        "case_id": case.id,
        "dataset_sha256": DATASET_HASH,
        "case_sha256": case_sha256(case),
        "answer_sha256": sha256_text(answer),
        "reviewer": {"reviewer_id": "reviewer-1", "protocol_version": SCORING_PROTOCOL_VERSION},
        "observed_response": case.expected_behavior.allowed_modes[0],
        "behavior_status": "pass",
        "behavior_reason": "人工核对行为与题目合同一致",
        "fact_reviews": [
            {"fact_id": fact.id, "status": "pass", "reason": "已核对型号、条件、单位和上限"}
            for fact in case.required_facts
        ],
        "claim_inventory_status": "complete",
        "claim_inventory_reason": "通读了完整答案",
        "claim_reviews": [
            {
                "claim_id": "c-1",
                "answer_span": answer,
                "cited_evidence_ids": ["ev-1"],
                "support_status": "supported",
                "supporting_spans": [{"evidence_id": "ev-1", "quote": TEXT}],
                "reason": "所附原文支持该型号在该条件下的上限",
            }
        ]
        if case.required_facts
        else [],
    }
    payload.update(changes)
    return BoundReview.model_validate(payload)


def _score(case, answer=ANSWER, review=None, *, run_id="run-1", **changes):
    return score_case(
        case, answer, dataset_sha256=DATASET_HASH, run_id=run_id, review=review, **changes
    )


def test_complete_bound_review_passes_without_inventing_new_semantic_labels():
    case = _case()
    grade = _score(case, review=_review(case))

    assert grade.strict_status == "pass"
    assert grade.review_status == "valid"
    assert grade.fact_statuses == {"f-1": "pass"}
    assert grade.dimensions["claim_support"].status == "pass"
    assert grade.answer_sha256 == sha256_text(ANSWER)


def test_literal_correct_number_without_review_does_not_establish_correctness():
    answer = "部件B推荐温度是245℃，也可能是999℃。"
    grade = _score(_case(), answer)

    assert grade.legacy_signals.literal_fact_value_hits == ["f-1"]
    assert grade.strict_status == "pending"
    assert grade.review_status == "missing"
    assert grade.fact_statuses == {"f-1": "pending"}


@pytest.mark.parametrize(
    "field", ["run_id", "case_id", "dataset_sha256", "case_sha256", "answer_sha256"]
)
def test_review_for_another_run_case_dataset_or_answer_is_not_reused(field):
    case = _case()
    review = _review(case)
    wrong = "another-id" if field.endswith("_id") else sha256_text("changed")
    review = review.model_copy(update={field: wrong})
    grade = _score(case, review=review)

    assert grade.review_status == "invalid"
    assert grade.strict_status == "pending"
    assert any("MISMATCH" in reason for reason in grade.review_errors)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_review_and_grade_version_numbers_reject_coercible_values(version):
    case = _case()
    review = _review(case)
    with pytest.raises(ValidationError, match="exact integer"):
        BoundReview.model_validate({**review.model_dump(), "review_schema_version": version})
    grade = _score(case, review=review)
    with pytest.raises(ValidationError, match="exact integer"):
        CaseGrade.model_validate({**grade.model_dump(), "grade_schema_version": version})


def test_old_boolean_labels_and_an_old_rubric_cannot_be_imported_as_new_pass():
    case = _case()
    payload = _review(case).model_dump()
    payload["answer_correct"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        BoundReview.model_validate(payload)
    with pytest.raises(ValidationError):
        ReviewerSource(reviewer_id="reviewer-1", protocol_version="legacy-rubric")


@pytest.mark.parametrize(
    "mode,answer",
    [
        ("refuse", "当前材料没有足够信息，无法确定。"),
        ("clarify", "请先提供部件型号。"),
    ],
)
def test_confirmed_claim_free_refusal_or_clarification_can_have_applicable_na(mode, answer):
    case = _case(mode)
    grade = _score(case, answer, _review(case, answer))

    assert grade.strict_status == "pass"
    assert grade.dimensions["claim_support"].status == "not_applicable"
    assert grade.dimensions["required_facts"].status == "not_applicable"
    assert grade.dimensions["behavior"].status == "pass"


@pytest.mark.parametrize("observed", ["refuse", "clarify", "mixed", "empty"])
def test_observed_response_cannot_copy_the_expected_answer_mode(observed):
    case = _case()
    grade = _score(case, review=_review(case, observed_response=observed))

    assert grade.strict_status == "fail"
    assert grade.dimensions["behavior"].status == "fail"


def test_refusal_followed_by_a_guess_cannot_pass_from_the_refusal_phrase():
    case = _case("refuse")
    answer = "没有足够信息，但我猜上限是245℃。"
    review = _review(case, answer, observed_response="mixed")
    grade = _score(case, answer, review)

    assert grade.strict_status == "fail"
    assert grade.dimensions["behavior"].status == "fail"


def test_required_answer_with_empty_claim_inventory_cannot_obtain_na():
    case = _case()
    grade = _score(case, review=_review(case, claim_reviews=[]))

    assert grade.strict_status == "pending"
    assert grade.dimensions["claim_support"].status != "not_applicable"


def test_repeated_handle_on_an_unreviewed_claim_does_not_hide_behind_a_reviewed_handle():
    case = _case()
    answer = ANSWER + " 另外声称推荐999℃。[[cite:doc_a:0001]]"
    payload = _review(case, answer).model_dump()
    payload["claim_reviews"][0]["answer_span"] = ANSWER
    grade = _score(case, answer, BoundReview.model_validate(payload))

    assert grade.review_status == "invalid"
    assert grade.strict_status == "pending"
    assert "CITATIONS_OUTSIDE_REVIEWED_CLAIMS" in grade.review_errors


@pytest.mark.parametrize(
    "changes",
    [
        {"fact_reviews": []},
        {"claim_inventory_status": "pending"},
        {"claim_inventory_status": "error"},
        {"observed_response": "unknown"},
        {"behavior_status": "pending"},
    ],
)
def test_missing_reviews_or_unconfirmed_inventory_prevent_strict_pass(changes):
    case = _case()
    grade = _score(case, review=_review(case, **changes))

    assert grade.strict_status == "pending"


def test_known_fact_failure_overrides_unreviewed_inventory_and_missing_artifacts():
    case = _case()
    review = _review(
        case,
        claim_inventory_status="pending",
        fact_reviews=[
            {
                "fact_id": "f-1",
                "status": "fail",
                "reason": "把上限错误地说成推荐工作点",
            }
        ],
    )
    grade = _score(case, review=review, artifact_status="incomplete")

    assert grade.strict_status == "fail"
    assert grade.fact_statuses["f-1"] == "fail"


def test_explicit_behavior_failure_is_not_hidden_by_unknown_response_intent():
    case = _case()
    grade = _score(case, review=_review(case, observed_response="unknown", behavior_status="fail"))

    assert grade.strict_status == "fail"


@pytest.mark.parametrize("execution_status", ["error", "timeout", "cancelled"])
def test_partial_output_and_complete_review_cannot_rescue_execution_failure(execution_status):
    case = _case()
    grade = _score(case, review=_review(case), execution_status=execution_status)

    assert grade.strict_status == "fail"


def test_empty_normal_response_is_a_known_failure_even_without_review():
    assert _score(_case(), "").strict_status == "fail"


@pytest.mark.parametrize("artifact_status", ["incomplete", "invalid"])
def test_complete_semantic_review_cannot_rescue_incomplete_evaluation_artifacts(artifact_status):
    case = _case()
    assert (
        _score(case, review=_review(case), artifact_status=artifact_status).strict_status
        == "pending"
    )


def test_unapproved_gold_cannot_produce_strict_pass():
    case = _case(gold_status="draft")
    grade = _score(case, review=_review(case))

    assert grade.strict_status == "pending"
    assert "GOLD_NOT_APPROVED" in grade.reason_codes


@pytest.mark.parametrize("support_status", ["insufficient", "contradicted"])
def test_attached_source_can_be_read_and_have_a_real_quote_but_still_not_support_claim(
    support_status,
):
    case = _case()
    review = _review(case)
    claim = review.claim_reviews[0].model_copy(update={"support_status": support_status})
    grade = _score(case, review=review.model_copy(update={"claim_reviews": [claim]}))

    assert grade.review_status == "valid"
    assert grade.strict_status == "fail"
    assert grade.dimensions["claim_support"].status == "fail"


def test_quote_from_another_claims_source_cannot_be_borrowed_as_support():
    case = _case()
    review = _review(case)
    other_text = "其他部件的说明。"
    other = GoldEvidence(
        id="ev-other",
        handle="doc_other:1",
        document_version="v1",
        text=other_text,
        content_sha256=sha256_text(other_text),
    )
    # Validate this syntactically sound review before evaluating its attachment.
    payload = review.model_dump()
    payload.update(evidence_snapshots=[other.model_dump()])
    payload["claim_reviews"][0]["supporting_spans"] = [
        {"evidence_id": "ev-other", "quote": other_text}
    ]
    grade = _score(case, review=BoundReview.model_validate(payload))

    assert grade.strict_status == "pending"
    assert "QUOTE_NOT_IN_CLAIM_CITATIONS" in grade.review_errors


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"supporting_spans": []}, "SUPPORTED_CLAIM_WITHOUT_QUOTE"),
        (
            {"supporting_spans": [{"evidence_id": "ev-1", "quote": "原文没有这句话"}]},
            "QUOTE_NOT_IN_SNAPSHOT",
        ),
        ({"cited_evidence_ids": []}, "CLAIM_CITATION_ATTACHMENT_MISMATCH"),
        ({"answer_span": "答案里没有的句子"}, "CLAIM_SPAN_MISSING_OR_AMBIGUOUS"),
    ],
)
def test_supported_label_with_invalid_evidence_or_attachment_does_not_pass(change, reason):
    case = _case()
    payload = _review(case).model_dump()
    payload["claim_reviews"][0].update(change)
    grade = _score(case, review=BoundReview.model_validate(payload))

    assert grade.review_status == "invalid"
    assert grade.strict_status == "pending"
    assert reason in grade.review_errors


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_new_evidence_id_cannot_replace_a_frozen_handle_with_another_body_or_version(version):
    case = _case()
    payload = _review(case).model_dump()
    fabricated = "原文被替换成另一个参数。"
    payload["evidence_snapshots"] = [
        {
            "id": "ev-new-id",
            "handle": "doc_a:0001",
            "document_version": version,
            "text": fabricated,
            "content_sha256": sha256_text(fabricated),
        }
    ]
    payload["claim_reviews"][0].update(
        cited_evidence_ids=["ev-new-id"],
        supporting_spans=[{"evidence_id": "ev-new-id", "quote": fabricated}],
    )
    grade = _score(case, review=BoundReview.model_validate(payload))

    assert grade.strict_status == "pending"
    assert "EVIDENCE_HANDLE_AMBIGUOUS" in grade.review_errors


def test_known_fact_failure_remains_failure_when_an_independent_support_review_is_invalid():
    case = _case()
    payload = _review(case).model_dump()
    payload["fact_reviews"][0]["status"] = "fail"
    payload["claim_reviews"][0]["supporting_spans"] = []
    grade = _score(case, review=BoundReview.model_validate(payload))

    assert grade.review_status == "invalid"
    assert grade.strict_status == "fail"


def test_duplicate_fact_review_is_rejected_instead_of_last_value_wins():
    payload = _review(_case()).model_dump()
    duplicate = copy.deepcopy(payload["fact_reviews"][0])
    duplicate["status"] = "fail"
    payload["fact_reviews"].append(duplicate)
    with pytest.raises(ValidationError, match="unique"):
        BoundReview.model_validate(payload)


def test_missing_plan_slot_unknown_review_and_timeout_all_remain_in_denominator():
    case = _case()
    passed = _score(case, review=_review(case))
    unknown = _score(case, run_id="run-2")
    timeout = _score(case, run_id="run-3", execution_status="timeout")
    summary = aggregate_grades(["run-1", "run-2", "run-3", "run-4"], [passed, unknown, timeout])

    assert (summary.planned_count, summary.started_count, summary.terminal_count) == (4, 3, 3)
    assert (
        summary.pass_count,
        summary.fail_count,
        summary.pending_count,
        summary.missing_count,
    ) == (1, 1, 2, 1)
    assert summary.strict_pass_rate == 0.25
    assert summary.strict_decision_coverage == 0.5
    assert summary.decided_pass_rate == 0.5
    assert summary.valid_review_coverage == 0.25


def test_not_started_slots_do_not_get_fake_terminal_times_or_review_credit():
    case = _case()
    grade = _score(case, "", execution_status="not_started")
    summary = aggregate_grades(["run-1"], [grade])

    assert grade.strict_status == "pending"
    assert summary.started_count == summary.terminal_count == 0
    assert summary.execution_completion_rate is None
    assert summary.strict_pass_rate == 0
    with pytest.raises(ValueError, match="not_started"):
        _score(case, review=_review(case), execution_status="not_started")


def test_primary_plan_rejects_duplicate_or_unplanned_grades_instead_of_choosing_best():
    grade = _score(_case())
    for plan, rows in [
        (["run-1"], [grade, grade]),
        (["other"], [grade]),
        (["run-1", "run-1"], [grade]),
    ]:
        with pytest.raises(ValueError):
            aggregate_grades(plan, rows)


def test_no_plan_has_no_numeric_success_rate():
    summary = aggregate_grades([], [])
    assert summary.planned_count == 0
    assert summary.strict_pass_rate is summary.terminal_coverage is None

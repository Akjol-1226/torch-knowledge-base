"""Twenty-two invented calibration examples, never business data or model results.

The labels are authored fixture expectations. ``synthetic-fixture-not-a-human``
is deliberately a test identifier, not evidence of a human audit. These tests
exercise content bindings and grade merging, not an automated semantic judge.
"""

from __future__ import annotations

import pytest

from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentEvalCase,
    canonical_sha256,
    case_sha256,
    sha256_text,
)
from evals.scoring.agent_quality import BoundReview, score_case

SOURCE = (
    "Entirely fictional Q-Unit, revision R7: in dry operating conditions, the maximum "
    "allowed temperature is 42 degC. Before startup, the permitted temperature range "
    "is 18 to 24 degC, inclusive. This text gives no recommended operating setpoint."
)
CITE = "[[cite:fictional-q:1]]"
CORRECT = "In dry operating conditions, Q-Unit R7 permits at most 42 degC." + CITE

# Each sample carries actual input/output, an explicit oracle label, and a
# rationale. No part of the production scorer infers these judgments from text.
SCENARIOS = [
    {
        "name": "correct-bound",
        "answer": CORRECT,
        "strict": "pass",
        "why": "Entity, condition, revision and upper-bound relationship all match.",
    },
    {
        "name": "equivalent-wording",
        "answer": "For dry operation of Q-Unit under R7, do not exceed 42 degC." + CITE,
        "strict": "pass",
        "why": "Different wording expresses the same bound.",
    },
    {
        "name": "wrong-unit",
        "answer": "Under R7, Q-Unit dry operation is limited to 42 K." + CITE,
        "fact": "fail",
        "support": "contradicted",
        "strict": "fail",
        "why": "K and degC are not interchangeable.",
    },
    {
        "name": "wrong-entity",
        "answer": "Z-Unit R7 dry operation is limited to 42 degC." + CITE,
        "fact": "fail",
        "support": "insufficient",
        "strict": "fail",
        "why": "The source states Q-Unit, not Z-Unit.",
    },
    {
        "name": "condition-overclaim",
        "answer": "Q-Unit R7 permits 42 degC in every condition." + CITE,
        "fact": "fail",
        "support": "insufficient",
        "strict": "fail",
        "why": "Dry operation cannot be generalized to all conditions.",
    },
    {
        "name": "wrong-revision",
        "answer": "Q-Unit R6 permits at most 42 degC in dry operation." + CITE,
        "fact": "fail",
        "support": "insufficient",
        "strict": "fail",
        "why": "An R7 source does not establish the R6 specification.",
    },
    {
        "name": "recommended-vs-maximum",
        "answer": "The recommended Q-Unit R7 operating setpoint is 42 degC." + CITE,
        "fact": "fail",
        "support": "insufficient",
        "strict": "fail",
        "why": "A maximum does not establish a recommendation; the source leaves the setpoint unknown.",
    },
    {
        "name": "correct-and-conflicting",
        "answer": CORRECT + "\nThe same limit is also 50 degC." + CITE,
        "fact": "fail",
        "support": "contradicted",
        "strict": "fail",
        "why": "A correct number does not erase a conflicting limit.",
    },
    {
        "name": "unsupported-extra-advice",
        "answer": CORRECT + "\nFor extra output, raise the limit to 50 degC." + CITE,
        "extra": True,
        "strict": "fail",
        "why": "Required fact is correct, but appended advice is unsupported.",
    },
    {
        "name": "right-fact-wrong-citation",
        "answer": CORRECT.replace(CITE, "[[cite:fictional-z:9]]"),
        "wrong_evidence": True,
        "support": "insufficient",
        "strict": "fail",
        "why": "A correct fact needs the actually attached source to support it.",
    },
    {
        "name": "missing-citation",
        "answer": CORRECT.replace(CITE, ""),
        "support": "insufficient",
        "strict": "fail",
        "why": "Gold text cannot silently substitute for a missing citation.",
    },
    {
        "name": "valid-refusal-no-factual-claim",
        "answer": "I cannot determine live stock from the supplied documents.",
        "mode": "refuse",
        "strict": "pass",
        "why": "A bounded refusal needs behavior review; claim support is NA.",
    },
    {
        "name": "unnecessary-refusal",
        "answer": "I cannot find the specified temperature.",
        "observed": "refuse",
        "fact": "fail",
        "no_claims": True,
        "strict": "fail",
        "why": "A nonempty refusal cannot pass an answerable task.",
    },
    {
        "name": "refusal-then-guess",
        "answer": "No live stock evidence is available. I guess the stock is 42 units.",
        "mode": "refuse",
        "observed": "mixed",
        "behavior": "fail",
        "has_claims": True,
        "support": "insufficient",
        "strict": "fail",
        "why": "The refusal phrase does not excuse an invented result.",
    },
    {
        "name": "necessary-clarification",
        "answer": "Which unit model and document revision do you mean?",
        "mode": "clarify",
        "strict": "pass",
        "why": "Asks for the missing model/revision without guessing.",
    },
    {
        "name": "irrelevant-clarification",
        "answer": "What is your preferred font?",
        "mode": "clarify",
        "behavior": "fail",
        "strict": "fail",
        "why": "Does not ask for the missing decision-critical slots.",
    },
    {
        "name": "unconfirmed-claim-inventory",
        "answer": CORRECT,
        "inventory": "pending",
        "strict": "pending",
        "why": "Known claims may pass while whole-answer coverage remains unconfirmed.",
    },
    {
        "name": "unreviewed-required-fact",
        "answer": CORRECT,
        "fact": None,
        "strict": "pending",
        "why": "Missing fact review cannot become a pass.",
    },
    {
        "name": "timeout-with-correct-partial",
        "answer": CORRECT,
        "execution": "timeout",
        "strict": "fail",
        "why": "Correct partial content does not satisfy the execution contract.",
    },
    {
        "name": "reversed-interval",
        "answer": "Q-Unit R7 startup temperature range is 24 to 18 degC, inclusive." + CITE,
        "interval": True,
        "fact": "fail",
        "support": "contradicted",
        "strict": "fail",
        "why": "Range direction and endpoints are meaningful.",
    },
    {
        "name": "correct-interval",
        "answer": "Q-Unit R7 startup temperature range is 18 to 24 degC, inclusive." + CITE,
        "interval": True,
        "strict": "pass",
        "why": "Both endpoints and inclusive boundaries match.",
    },
    {
        "name": "evidence-artifacts-incomplete",
        "answer": CORRECT,
        "artifacts": "incomplete",
        "strict": "pending",
        "why": "A passing content review cannot hide missing required artifacts.",
    },
]


def calibration_inputs(scenario):
    mode = scenario.get("mode", "answer")
    expected = {"allowed_modes": [mode]}
    if mode == "clarify":
        expected["missing_slots"] = ["model", "revision"]
    case_data = {
        "id": "fictional-" + scenario["name"],
        "document_group_id": "fictional-family-only",
        "input": {
            "messages": [{"role": "user", "content": "What is Q-Unit R7's dry operating limit?"}],
            "kbs": ["fictional-calibration-only"],
        },
        "expected_behavior": expected,
        "gold_status": "approved",
        "required_facts": [],
        "evidence_groups": [],
        "gold_evidence": [],
    }
    if mode == "refuse":
        case_data["input"]["messages"][0]["content"] = "What is the live stock level?"
    elif mode == "clarify":
        case_data["input"]["messages"][0]["content"] = "What temperature does the unit permit?"
    else:
        fact = {
            "id": "f-limit",
            "kind": "numeric_constraint",
            "entity": "Q-Unit",
            "attribute": "temperature",
            "relation": "upper_bound",
            "value": "42",
            "unit": "degC",
            "conditions": ["dry operation"],
            "document_version": "R7",
            "evidence_group_ids": ["g-limit"],
        }
        if scenario.get("interval"):
            fact.update(
                relation="interval",
                value=None,
                lower="18",
                upper="24",
                lower_inclusive=True,
                upper_inclusive=True,
                conditions=["before startup"],
            )
            case_data["input"]["messages"][0]["content"] = "What is Q-Unit R7's startup interval?"
        case_data["required_facts"] = [fact]
        case_data["evidence_groups"] = [{"id": "g-limit", "equivalent_evidence_ids": ["ev-q"]}]
        case_data["gold_evidence"] = [
            {
                "id": "ev-q",
                "handle": "fictional-q:1",
                "document_version": "R7",
                "text": SOURCE,
                "content_sha256": sha256_text(SOURCE),
            }
        ]
    case = AgentEvalCase.model_validate(case_data)
    dataset_hash = canonical_sha256({"fixture_only": case.model_dump(mode="json")})
    review_data = {
        "run_id": "synthetic-" + scenario["name"],
        "case_id": case.id,
        "dataset_sha256": dataset_hash,
        "case_sha256": case_sha256(case),
        "answer_sha256": sha256_text(scenario["answer"]),
        "reviewer": {
            "reviewer_id": "synthetic-fixture-not-a-human",
            "protocol_version": SCORING_PROTOCOL_VERSION,
        },
        "observed_response": scenario.get("observed", mode),
        "behavior_status": scenario.get("behavior", "pass"),
        "behavior_reason": scenario["why"],
        "fact_reviews": [],
        "claim_inventory_status": scenario.get("inventory", "complete"),
        "claim_inventory_reason": "Synthetic authored fixture: " + scenario["why"],
        "claim_reviews": [],
        "evidence_snapshots": [],
    }
    if case.required_facts and scenario.get("fact", "pass") is not None:
        review_data["fact_reviews"] = [
            {
                "fact_id": "f-limit",
                "status": scenario.get("fact", "pass"),
                "reason": scenario["why"],
            }
        ]
    has_claims = mode == "answer" or scenario.get("has_claims")
    if has_claims and not scenario.get("no_claims"):
        for index, part in enumerate(scenario["answer"].split("\n"), start=1):
            support = scenario.get("support", "supported")
            if scenario["name"] == "correct-and-conflicting" and index == 1:
                support = "supported"
            if scenario.get("extra") and index == 2:
                support = "insufficient"
            evidence_id = "ev-q" if CITE in part else None
            if scenario.get("wrong_evidence"):
                evidence_id = "ev-z"
            review_data["claim_reviews"].append(
                {
                    "claim_id": f"claim-{index}",
                    "answer_span": part,
                    "cited_evidence_ids": [evidence_id] if evidence_id else [],
                    "support_status": support,
                    "reason": scenario["why"],
                    "supporting_spans": [{"evidence_id": evidence_id, "quote": SOURCE}]
                    if support == "supported"
                    else [],
                }
            )
    if scenario.get("wrong_evidence"):
        other_text = "Entirely fictional Z-Unit R7 uses an unrelated limit of 90 degC."
        review_data["evidence_snapshots"] = [
            {
                "id": "ev-z",
                "handle": "fictional-z:9",
                "document_version": "R7",
                "text": other_text,
                "content_sha256": sha256_text(other_text),
            }
        ]
    return case, dataset_hash, BoundReview.model_validate(review_data)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[item["name"] for item in SCENARIOS])
def test_synthetic_calibration_contract(scenario):
    case, dataset_hash, review = calibration_inputs(scenario)
    grade = score_case(
        case,
        scenario["answer"],
        dataset_sha256=dataset_hash,
        run_id=review.run_id,
        review=review,
        execution_status=scenario.get("execution", "completed"),
        artifact_status=scenario.get("artifacts", "complete"),
    )
    assert grade.review_status == "valid", grade.review_errors
    assert grade.strict_status == scenario["strict"]
    if scenario["name"] == "unsupported-extra-advice":
        assert grade.dimensions["required_facts"].status == "pass"
        assert grade.dimensions["claim_support"].status == "fail"
    if scenario["name"] == "valid-refusal-no-factual-claim":
        assert grade.dimensions["claim_support"].status == "not_applicable"
    if scenario["name"] == "timeout-with-correct-partial":
        assert grade.dimensions["required_facts"].status == "pass"


def test_calibration_examples_are_distinct_and_cover_protocol_boundaries():
    names = [item["name"] for item in SCENARIOS]
    assert len(names) == len(set(names)) == 22
    assert {item["strict"] for item in SCENARIOS} == {"pass", "fail", "pending"}
    assert {item.get("mode", "answer") for item in SCENARIOS} == {"answer", "refuse", "clarify"}

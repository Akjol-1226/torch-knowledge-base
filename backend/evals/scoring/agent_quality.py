"""Merge explicitly bound human reviews without inventing semantic labels.

Citation markup and exact quotes are checked mechanically. Entailment, response
intent and factual correctness remain recorded human judgments, never substring
or legacy-boolean guesses. A reviewer identifier records provenance; it does not
authenticate the reviewer or turn this module into independent certification.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from evals.agent_schemas import (
    SCORING_PROTOCOL_VERSION,
    AgentEvalCase,
    ArtifactStatus,
    ClaimInventoryStatus,
    ExecutionStatus,
    GoldEvidence,
    GradeStatus,
    Identifier,
    NonBlankText,
    ObservedResponseMode,
    Sha256,
    StrictModel,
    case_sha256,
    sha256_text,
)

_CITE_RE = re.compile(r"\[\[cite:([^\]]+)\]\]")
_TERMINAL_STATUSES = frozenset({"completed", "error", "timeout", "cancelled"})


class ReviewerSource(StrictModel):
    kind: Literal["human"] = "human"
    reviewer_id: Identifier
    protocol_version: Literal["torch-agent-score/2.0-draft"]


class FactReview(StrictModel):
    fact_id: Identifier
    status: Literal["pass", "fail", "pending", "error"]
    reason: NonBlankText


class EvidenceSpan(StrictModel):
    evidence_id: Identifier
    quote: NonBlankText


class ClaimReview(StrictModel):
    claim_id: Identifier
    # Include the claim's attached [[cite:handle]] markers in this exact span.
    # Unique spans avoid guessing which repeated sentence a review describes.
    answer_span: NonBlankText
    cited_evidence_ids: list[Identifier] = Field(default_factory=list)
    support_status: Literal["supported", "contradicted", "insufficient", "pending", "error"]
    supporting_spans: list[EvidenceSpan] = Field(default_factory=list)
    reason: NonBlankText

    @model_validator(mode="after")
    def unique_citations(self) -> ClaimReview:
        if len(self.cited_evidence_ids) != len(set(self.cited_evidence_ids)):
            raise ValueError("claim cited_evidence_ids must be unique")
        return self


class BoundReview(StrictModel):
    review_schema_version: Literal[1] = 1
    run_id: Identifier
    case_id: Identifier
    dataset_sha256: Sha256
    case_sha256: Sha256
    answer_sha256: Sha256
    reviewer: ReviewerSource
    observed_response: ObservedResponseMode
    behavior_status: Literal["pass", "fail", "pending", "error"]
    behavior_reason: NonBlankText
    fact_reviews: list[FactReview] = Field(default_factory=list)
    claim_inventory_status: ClaimInventoryStatus
    claim_inventory_reason: NonBlankText
    claim_reviews: list[ClaimReview] = Field(default_factory=list)
    evidence_snapshots: list[GoldEvidence] = Field(default_factory=list)

    @field_validator("review_schema_version", mode="before")
    @classmethod
    def exact_version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("review_schema_version must be an exact integer")
        return value

    @model_validator(mode="after")
    def unique_review_items(self) -> BoundReview:
        for label, identities in (
            ("facts", [item.fact_id for item in self.fact_reviews]),
            ("claims", [item.claim_id for item in self.claim_reviews]),
            ("evidence", [item.id for item in self.evidence_snapshots]),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"review {label} IDs must be unique")
        return self


class DimensionGrade(StrictModel):
    status: GradeStatus
    reasons: list[str] = Field(default_factory=list)


class LegacySignals(StrictModel):
    """Literal presence is a diagnostic signal, never a correctness score."""

    required_fact_count: int = Field(ge=0)
    literal_fact_value_hits: list[str] = Field(default_factory=list)


class CaseGrade(StrictModel):
    grade_schema_version: Literal[1] = 1
    run_id: Identifier
    case_id: Identifier
    dataset_sha256: Sha256
    case_sha256: Sha256
    answer_sha256: Sha256
    execution_status: ExecutionStatus
    artifact_status: ArtifactStatus
    review_status: Literal["missing", "valid", "invalid"]
    review_errors: list[str] = Field(default_factory=list)
    dimensions: dict[str, DimensionGrade]
    fact_statuses: dict[str, GradeStatus] = Field(default_factory=dict)
    strict_status: Literal["pass", "fail", "pending"]
    reason_codes: list[str] = Field(default_factory=list)
    legacy_signals: LegacySignals

    @field_validator("grade_schema_version", mode="before")
    @classmethod
    def exact_version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("grade_schema_version must be an exact integer")
        return value


class GradeSummary(StrictModel):
    planned_count: int = Field(ge=0)
    started_count: int = Field(ge=0)
    terminal_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    valid_review_count: int = Field(ge=0)
    invalid_review_count: int = Field(ge=0)
    started_coverage: float | None
    terminal_coverage: float | None
    execution_completion_rate: float | None
    strict_pass_rate: float | None
    strict_decision_coverage: float | None
    decided_pass_rate: float | None
    valid_review_coverage: float | None


def _dimension(status: GradeStatus, *reasons: str) -> DimensionGrade:
    return DimensionGrade(status=status, reasons=list(reasons))


def _merge(statuses: Sequence[GradeStatus]) -> GradeStatus:
    if "fail" in statuses:
        return "fail"
    if "error" in statuses:
        return "error"
    if "pending" in statuses:
        return "pending"
    return "pass"


def _review_errors(
    case: AgentEvalCase,
    answer: str,
    review: BoundReview,
    *,
    dataset_hash: str,
    run_id: str,
) -> list[str]:
    errors: list[str] = []
    if review.reviewer.protocol_version != SCORING_PROTOCOL_VERSION:
        errors.append("REVIEW_PROTOCOL_MISMATCH")
    if review.reviewer.kind != "human":
        errors.append("REVIEW_SOURCE_KIND_MISMATCH")
    for name, actual, expected in (
        ("RUN_ID", review.run_id, run_id),
        ("CASE_ID", review.case_id, case.id),
        ("DATASET_HASH", review.dataset_sha256, dataset_hash),
        ("CASE_HASH", review.case_sha256, case_sha256(case)),
        ("ANSWER_HASH", review.answer_sha256, sha256_text(answer)),
    ):
        if actual != expected:
            errors.append(f"REVIEW_{name}_MISMATCH")
    fact_ids = {fact.id for fact in case.required_facts}
    if any(item.fact_id not in fact_ids for item in review.fact_reviews):
        errors.append("REVIEW_UNKNOWN_FACT")

    evidence = {item.id: item for item in case.gold_evidence}
    for item in review.evidence_snapshots:
        if item.id in evidence and item != evidence[item.id]:
            errors.append("EVIDENCE_ID_CONTENT_MISMATCH")
        evidence[item.id] = item
    if len({item.handle for item in evidence.values()}) != len(evidence):
        errors.append("EVIDENCE_HANDLE_AMBIGUOUS")
    if any(sha256_text(item.text) != item.content_sha256 for item in evidence.values()):
        errors.append("EVIDENCE_CONTENT_HASH_MISMATCH")

    reviewed_intervals: list[tuple[int, int]] = []
    for claim in review.claim_reviews:
        if answer.count(claim.answer_span) != 1:
            errors.append("CLAIM_SPAN_MISSING_OR_AMBIGUOUS")
        else:
            start = answer.index(claim.answer_span)
            reviewed_intervals.append((start, start + len(claim.answer_span)))
        attached = set(claim.cited_evidence_ids)
        if not attached.issubset(evidence):
            errors.append("CLAIM_UNKNOWN_EVIDENCE")
        expected_handles = {evidence[item].handle for item in attached if item in evidence}
        if set(_CITE_RE.findall(claim.answer_span)) != expected_handles:
            errors.append("CLAIM_CITATION_ATTACHMENT_MISMATCH")
        if claim.support_status == "supported" and not claim.supporting_spans:
            errors.append("SUPPORTED_CLAIM_WITHOUT_QUOTE")
        for span in claim.supporting_spans:
            if span.evidence_id not in attached:
                errors.append("QUOTE_NOT_IN_CLAIM_CITATIONS")
            source = evidence.get(span.evidence_id)
            if source is None or span.quote not in source.text:
                errors.append("QUOTE_NOT_IN_SNAPSHOT")
    if any(
        not any(start <= match.start() and match.end() <= end for start, end in reviewed_intervals)
        for match in _CITE_RE.finditer(answer)
    ):
        errors.append("CITATIONS_OUTSIDE_REVIEWED_CLAIMS")
    return list(dict.fromkeys(errors))


def _review_dimensions(
    case: AgentEvalCase, review: BoundReview
) -> tuple[dict[str, DimensionGrade], dict[str, GradeStatus]]:
    allowed = case.expected_behavior.allowed_modes[0]
    if review.behavior_status == "fail":
        behavior = _dimension("fail", "BOUND_HUMAN_BEHAVIOR_REVIEW")
    elif review.observed_response == "unknown":
        behavior = _dimension("pending", "RESPONSE_INTENT_UNKNOWN")
    elif review.observed_response != allowed:
        behavior = _dimension("fail", "RESPONSE_MODE_MISMATCH")
    else:
        behavior = _dimension(review.behavior_status, "BOUND_HUMAN_BEHAVIOR_REVIEW")

    by_fact = {item.fact_id: item for item in review.fact_reviews}
    fact_statuses: dict[str, GradeStatus] = {
        fact.id: by_fact[fact.id].status if fact.id in by_fact else "pending"
        for fact in case.required_facts
    }
    facts = (
        _dimension(_merge(list(fact_statuses.values())), "BOUND_HUMAN_FACT_REVIEW")
        if fact_statuses
        else _dimension("not_applicable", "NO_REQUIRED_FACTS")
    )
    inventory = _dimension(
        "pass" if review.claim_inventory_status == "complete" else review.claim_inventory_status,
        "BOUND_HUMAN_CLAIM_INVENTORY",
    )
    support_map: dict[str, GradeStatus] = {
        "supported": "pass",
        "contradicted": "fail",
        "insufficient": "fail",
        "pending": "pending",
        "error": "error",
    }
    if review.claim_reviews:
        support = _dimension(
            _merge([support_map[item.support_status] for item in review.claim_reviews]),
            "BOUND_HUMAN_CLAIM_SUPPORT_REVIEW",
        )
    elif (
        review.claim_inventory_status == "complete"
        and not case.required_facts
        and review.observed_response in {"refuse", "clarify"}
    ):
        support = _dimension("not_applicable", "CONFIRMED_NO_FACTUAL_CLAIMS")
    else:
        support = _dimension("pending", "NO_CONFIRMED_CLAIM_REVIEW")
        if inventory.status == "pass":
            inventory = _dimension("pending", "ANSWER_WITHOUT_CLAIM_INVENTORY")
    return {
        "behavior": behavior,
        "required_facts": facts,
        "claim_inventory": inventory,
        "claim_support": support,
    }, fact_statuses


def score_case(
    case: AgentEvalCase,
    answer: str,
    *,
    dataset_sha256: str,
    run_id: str,
    review: BoundReview | None = None,
    execution_status: ExecutionStatus = "completed",
    artifact_status: ArtifactStatus = "complete",
) -> CaseGrade:
    """Score only what an exact-bound human review can establish.

    The caller supplies the hash of the frozen dataset it independently loaded.
    This function binds it to the review; it cannot authenticate that dataset or
    prove a runtime trace complete. Legacy literal hits never decide a grade.
    """
    if execution_status == "not_started" and (answer or review is not None):
        raise ValueError("not_started slots cannot contain an actual answer or review")
    errors = (
        _review_errors(case, answer, review, dataset_hash=dataset_sha256, run_id=run_id)
        if review is not None
        else []
    )
    fact_statuses: dict[str, GradeStatus]
    binding_invalid = any(
        error.startswith("REVIEW_") and error.endswith("_MISMATCH") for error in errors
    )
    if review is not None and not binding_invalid:
        review_status: Literal["missing", "valid", "invalid"] = "invalid" if errors else "valid"
        dimensions, fact_statuses = _review_dimensions(case, review)
        if errors:
            # Quote/attachment failures cannot establish support. Independent
            # bound fact/behavior failures still take precedence over pending.
            dimensions["claim_support"] = _dimension("error", "REVIEW_EVIDENCE_INVALID")
            dimensions["claim_inventory"] = _dimension("pending", "REVIEW_EVIDENCE_INVALID")
    else:
        review_status = "invalid" if errors else "missing"
        dimensions = {
            name: _dimension("pending", "REVIEW_INVALID" if errors else "REVIEW_MISSING")
            for name in ("behavior", "claim_inventory", "claim_support")
        }
        fact_statuses = {fact.id: "pending" for fact in case.required_facts}
        dimensions["required_facts"] = (
            _dimension("pending", "REQUIRED_FACTS_UNREVIEWED")
            if fact_statuses
            else _dimension("not_applicable", "NO_REQUIRED_FACTS")
        )

    reasons: list[str] = []
    if execution_status in {"error", "timeout", "cancelled"}:
        reasons.append("EXECUTION_CONTRACT_FAILED")
    if execution_status == "completed" and not answer.strip():
        reasons.append("EMPTY_RESPONSE")
    reasons.extend(
        f"DIMENSION_FAILED:{key}" for key, item in dimensions.items() if item.status == "fail"
    )
    if reasons:
        strict_status: Literal["pass", "fail", "pending"] = "fail"
    else:
        if execution_status == "not_started":
            reasons.append("EXECUTION_NOT_STARTED")
        if artifact_status != "complete":
            reasons.append("ARTIFACT_NOT_COMPLETE")
        if case.gold_status != "approved":
            reasons.append("GOLD_NOT_APPROVED")
        reasons.extend(
            f"DIMENSION_UNRESOLVED:{key}"
            for key, item in dimensions.items()
            if item.status in {"pending", "error"}
        )
        strict_status = "pending" if reasons else "pass"

    folded = answer.casefold()
    literal_hits = [
        fact.id
        for fact in case.required_facts
        if any(value and value.casefold() in folded for value in [fact.value, *fact.aliases])
    ]
    return CaseGrade(
        run_id=run_id,
        case_id=case.id,
        dataset_sha256=dataset_sha256,
        case_sha256=case_sha256(case),
        answer_sha256=sha256_text(answer),
        execution_status=execution_status,
        artifact_status=artifact_status,
        review_status=review_status,
        review_errors=errors,
        dimensions=dimensions,
        fact_statuses=fact_statuses,
        strict_status=strict_status,
        reason_codes=reasons,
        legacy_signals=LegacySignals(
            required_fact_count=len(case.required_facts), literal_fact_value_hits=literal_hits
        ),
    )


def aggregate_grades(planned_run_ids: Sequence[str], grades: Sequence[CaseGrade]) -> GradeSummary:
    """Keep missing and unresolved slots in the predeclared plan denominator."""
    planned = set(planned_run_ids)
    if len(planned) != len(planned_run_ids):
        raise ValueError("planned run IDs must be unique")
    observed = {grade.run_id for grade in grades}
    if len(observed) != len(grades):
        raise ValueError("duplicate run grades; select the primary trial explicitly")
    if not observed.issubset(planned):
        raise ValueError("grade is outside the frozen plan")
    total = len(planned)
    started = sum(grade.execution_status != "not_started" for grade in grades)
    terminal = sum(grade.execution_status in _TERMINAL_STATUSES for grade in grades)
    completed = sum(grade.execution_status == "completed" for grade in grades)
    passed = sum(grade.strict_status == "pass" for grade in grades)
    failed = sum(grade.strict_status == "fail" for grade in grades)
    valid = sum(grade.review_status == "valid" for grade in grades)

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return GradeSummary(
        planned_count=total,
        started_count=started,
        terminal_count=terminal,
        completed_count=completed,
        missing_count=total - len(grades),
        pass_count=passed,
        fail_count=failed,
        pending_count=total - passed - failed,
        valid_review_count=valid,
        invalid_review_count=sum(grade.review_status == "invalid" for grade in grades),
        started_coverage=rate(started, total),
        terminal_coverage=rate(terminal, total),
        execution_completion_rate=rate(completed, started),
        strict_pass_rate=rate(passed, total),
        strict_decision_coverage=rate(passed + failed, total),
        decided_pass_rate=rate(passed, passed + failed),
        valid_review_coverage=rate(valid, total),
    )

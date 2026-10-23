"""Strict, offline contracts for the second Agent evaluation protocol.

These models validate annotations and their content bindings. They do not infer
that evidence alternatives are semantically equivalent or that an answer is
correct. No application modules, model clients, or live stores are imported.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

DATASET_SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 3
SCORING_PROTOCOL_VERSION = "torch-agent-score/2.0-draft"

GradeStatus = Literal["pass", "fail", "pending", "error", "not_applicable"]
ExecutionStatus = Literal["completed", "error", "timeout", "cancelled", "not_started"]
ArtifactStatus = Literal["complete", "incomplete", "invalid"]
ResponseMode = Literal["answer", "refuse", "clarify"]
ObservedResponseMode = Literal["answer", "refuse", "clarify", "mixed", "empty", "unknown"]
ClaimInventoryStatus = Literal["complete", "pending", "error"]
GoldStatus = Literal["draft", "approved", "needs_review"]
FactKind = Literal["numeric_constraint", "text", "action"]
FactRelation = Literal["equal", "upper_bound", "lower_bound", "interval", "requires"]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _identifier(value: str) -> str:
    if not value or any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("identifiers must be nonempty and contain no whitespace or controls")
    return value


NonBlankText = Annotated[str, AfterValidator(_nonblank)]
Identifier = Annotated[str, AfterValidator(_identifier)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _integer_version(value: Any) -> Any:
    if type(value) is not int:
        raise ValueError("schema version must be an integer, not bool/float/string")
    return value


DatasetSchemaVersion = Annotated[Literal[2], BeforeValidator(_integer_version)]
ResultSchemaVersion = Annotated[Literal[3], BeforeValidator(_integer_version)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, allow_inf_nan=False
    )


def _check_json(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON numbers are forbidden")
        return
    if type(value) is list:
        for item in value:
            _check_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _check_json(item)
        return
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize JSON values with stable keys; never normalize source text.

    Pydantic models are explicitly converted to their JSON-mode projection.
    Other Python objects (including Decimal, tuples, sets and non-string keys)
    are rejected rather than silently coerced.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    _check_json(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_text(text: str) -> str:
    """Hash the exact UTF-8 text, including whitespace and trailing newlines."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _decimal(value: str, label: str) -> Decimal:
    # Annotations are decimal literals, not Python/Decimal expressions or units.
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", value, flags=re.ASCII):
        raise ValueError(f"{label} must be a plain decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite decimal")
    return parsed


class EvalMessage(StrictModel):
    role: Literal["user"]
    content: NonBlankText


class EvalInput(StrictModel):
    messages: list[EvalMessage] = Field(min_length=1, max_length=1)
    kbs: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_kbs(self) -> EvalInput:
        _unique(self.kbs, "KB identifiers")
        return self


class ExpectedBehavior(StrictModel):
    allowed_modes: list[ResponseMode] = Field(min_length=1, max_length=1)
    missing_slots: list[Identifier] = Field(default_factory=list)
    must_not_claim: list[NonBlankText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_missing_slots(self) -> ExpectedBehavior:
        _unique(self.missing_slots, "missing slots")
        _unique(self.must_not_claim, "prohibited claims")
        if self.allowed_modes[0] == "clarify":
            if not self.missing_slots:
                raise ValueError("clarify requires explicit missing_slots")
        elif self.missing_slots:
            raise ValueError("missing_slots only applies to clarify")
        return self


class GoldEvidence(StrictModel):
    """A full immutable node snapshot, not an unanchored partial excerpt."""

    id: Identifier
    handle: Identifier
    document_version: Identifier
    text: NonBlankText
    content_sha256: Sha256

    @model_validator(mode="after")
    def validate_content_hash(self) -> GoldEvidence:
        if sha256_text(self.text) != self.content_sha256:
            raise ValueError("content_sha256 does not match exact evidence text")
        return self


class EvidenceGroup(StrictModel):
    id: Identifier
    equivalent_evidence_ids: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_members(self) -> EvidenceGroup:
        _unique(self.equivalent_evidence_ids, "evidence group members")
        return self


class AtomicFact(StrictModel):
    id: Identifier
    kind: FactKind
    entity: NonBlankText
    attribute: Identifier
    relation: FactRelation
    value: NonBlankText | None = None
    unit: NonBlankText | None = None
    lower: NonBlankText | None = None
    upper: NonBlankText | None = None
    lower_inclusive: bool | None = None
    upper_inclusive: bool | None = None
    conditions: list[NonBlankText] = Field(default_factory=list)
    document_version: Identifier
    evidence_group_ids: list[Identifier] = Field(min_length=1)
    aliases: list[NonBlankText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_value_contract(self) -> AtomicFact:
        _unique(self.evidence_group_ids, "fact evidence groups")
        _unique(self.conditions, "fact conditions")
        _unique(self.aliases, "fact aliases")
        bounds = (self.lower, self.upper, self.lower_inclusive, self.upper_inclusive)
        if self.kind != "numeric_constraint":
            expected_relation = "requires" if self.kind == "action" else "equal"
            if self.relation != expected_relation:
                raise ValueError(f"{self.kind} requires relation={expected_relation}")
            if self.value is None or self.unit is not None or any(v is not None for v in bounds):
                raise ValueError("text/action facts require value and no numeric fields")
            return self
        if self.relation not in {"equal", "upper_bound", "lower_bound", "interval"}:
            raise ValueError("numeric facts require a numeric relation")
        if self.unit is None or self.unit.strip() != self.unit:
            raise ValueError("numeric facts require an explicit, trimmed unit (1 if dimensionless)")
        if self.aliases:
            raise ValueError("numeric aliases are not a substitute for explicit unit conversion")
        if self.relation == "interval":
            if self.value is not None or any(v is None for v in bounds):
                raise ValueError(
                    "interval requires both bounds and inclusivity, without scalar value"
                )
            if self.lower is None or self.upper is None:
                raise ValueError("interval bounds are required")
            lower, upper = _decimal(self.lower, "lower"), _decimal(self.upper, "upper")
            if lower > upper or (
                lower == upper and not (self.lower_inclusive and self.upper_inclusive)
            ):
                raise ValueError("interval bounds describe an empty or reversed range")
        else:
            if self.value is None or any(v is not None for v in bounds):
                raise ValueError("scalar constraint requires value and no interval fields")
            _decimal(self.value, "value")
        return self


class AgentEvalCase(StrictModel):
    id: Identifier
    document_group_id: Identifier
    categories: list[Identifier] = Field(default_factory=list)
    input: EvalInput
    expected_behavior: ExpectedBehavior
    gold_status: GoldStatus = "draft"
    required_facts: list[AtomicFact] = Field(default_factory=list)
    evidence_groups: list[EvidenceGroup] = Field(default_factory=list)
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    reference_answer: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_references(self) -> AgentEvalCase:
        _unique(self.categories, "case categories")
        _unique([fact.id for fact in self.required_facts], "fact IDs")
        _unique([group.id for group in self.evidence_groups], "evidence group IDs")
        _unique([evidence.id for evidence in self.gold_evidence], "evidence IDs")
        _unique([evidence.handle for evidence in self.gold_evidence], "evidence handles")
        evidence_by_id = {evidence.id: evidence for evidence in self.gold_evidence}
        groups = {group.id: group for group in self.evidence_groups}
        used: set[str] = set()
        for group in self.evidence_groups:
            for evidence_id in group.equivalent_evidence_ids:
                if evidence_id not in evidence_by_id:
                    raise ValueError(f"unknown evidence ID in group: {evidence_id}")
                if evidence_id in used:
                    raise ValueError("equivalent evidence groups must be disjoint")
                used.add(evidence_id)
        for fact in self.required_facts:
            for group_id in fact.evidence_group_ids:
                if group_id not in groups:
                    raise ValueError(f"unknown evidence group for fact: {group_id}")
                for evidence_id in groups[group_id].equivalent_evidence_ids:
                    if evidence_by_id[evidence_id].document_version != fact.document_version:
                        raise ValueError("fact and supporting evidence document versions differ")
        if self.gold_status == "approved" and self.expected_behavior.allowed_modes == ["answer"]:
            if not self.required_facts:
                raise ValueError("approved answer cases require at least one required fact")
        return self


class AgentDataset(StrictModel):
    dataset_schema_version: DatasetSchemaVersion
    dataset_id: Identifier
    annotation_version: Identifier
    scoring_protocol_version: Literal["torch-agent-score/2.0-draft"] = SCORING_PROTOCOL_VERSION
    cases: list[AgentEvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identities(self) -> AgentDataset:
        _unique([case.id for case in self.cases], "case IDs")
        # Reuse across cases is allowed only when an ID denotes identical bytes
        # and provenance, so a content reference cannot become ambiguous.
        evidence_by_id: dict[str, GoldEvidence] = {}
        node_hashes: dict[tuple[str, str], str] = {}
        for case in self.cases:
            for evidence in case.gold_evidence:
                previous = evidence_by_id.get(evidence.id)
                if previous is not None and previous != evidence:
                    raise ValueError("evidence ID has inconsistent content/provenance across cases")
                evidence_by_id[evidence.id] = evidence
                node_key = (evidence.handle, evidence.document_version)
                if node_key in node_hashes and node_hashes[node_key] != evidence.content_sha256:
                    raise ValueError("node handle/version has inconsistent snapshot content")
                node_hashes[node_key] = evidence.content_sha256
        return self


def case_sha256(case: AgentEvalCase) -> str:
    """Bind the complete validated case, including input, gold and defaults."""
    return canonical_sha256(case)

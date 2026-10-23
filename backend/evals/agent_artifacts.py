"""Offline evaluation inputs and append-only, content-bound assessment artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from evals.agent_schemas import (
    AgentDataset,
    ArtifactStatus,
    ExecutionStatus,
    ResultSchemaVersion,
    StrictModel,
    canonical_json,
    canonical_sha256,
    case_sha256,
    sha256_text,
)

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite JSON value")


def strict_json(text: str) -> Any:
    """Reject duplicate keys and non-finite values before schema validation."""
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        canonical_json(value)
        return value
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid JSON input") from exc


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds size limit")
    return data


def load_json(path: Path) -> Any:
    try:
        return strict_json(read_bytes(path).decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("input is not UTF-8") from exc


def parse_jsonl(data: bytes) -> list[dict[str, Any]]:
    if data and not data.endswith(b"\n"):
        raise ValueError("JSONL input has an unsealed final line")
    rows: list[dict[str, Any]] = []
    for line in data.splitlines():
        if not line.strip() or len(line) > MAX_LINE_BYTES:
            raise ValueError("invalid JSONL line")
        try:
            row = strict_json(line.decode("utf-8"))
        except UnicodeError as exc:
            raise ValueError("JSONL input is not UTF-8") from exc
        if not isinstance(row, dict):
            raise ValueError("JSONL rows must be objects")
        rows.append(row)
    return rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return parse_jsonl(read_bytes(path))


class EvaluationPlan(StrictModel):
    experiment_schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    arm_ids: list[str] = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)
    repeats: StrictInt = Field(ge=1, le=100)
    primary_trial_index: Literal[1] = 1

    @field_validator("experiment_schema_version", "primary_trial_index", mode="before")
    @classmethod
    def validate_integer_literals(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("version and trial index must be integers")
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> EvaluationPlan:
        for values in (self.arm_ids, self.case_ids):
            if len(values) != len(set(values)) or any(not item.strip() for item in values):
                raise ValueError("plan identities must be unique and non-blank")
        if not self.experiment_id.strip():
            raise ValueError("experiment identity must not be blank")
        return self

    def slots(self) -> set[tuple[str, str, int]]:
        return {
            (arm, case, repeat)
            for arm in self.arm_ids
            for case in self.case_ids
            for repeat in range(1, self.repeats + 1)
        }


class RunRecord(StrictModel):
    result_schema_version: ResultSchemaVersion = 3
    experiment_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    repeat_index: StrictInt = Field(ge=1)
    trial_index: Literal[1] = 1
    run_id: str = Field(min_length=1)
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer: str
    execution_status: ExecutionStatus
    artifact_status: ArtifactStatus
    output_kind: Literal["final", "partial"] = "final"
    duration_ms: float | None = None

    @field_validator("trial_index", mode="before")
    @classmethod
    def validate_trial_type(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("trial index must be an integer")
        return value

    @model_validator(mode="after")
    def validate_run(self) -> RunRecord:
        for identity in (self.experiment_id, self.arm_id, self.case_id, self.run_id):
            if not identity.strip():
                raise ValueError("run identities must not be blank")
        if self.answer_sha256 != sha256_text(self.answer):
            raise ValueError("answer content hash mismatch")
        if self.execution_status == "not_started":
            raise ValueError("not-started slots cannot create execution records")
        if self.duration_ms is not None and (
            not math.isfinite(self.duration_ms) or self.duration_ms < 0
        ):
            raise ValueError("duration must be finite and non-negative")
        if self.execution_status == "completed" and self.output_kind != "final":
            raise ValueError("completed execution requires final output")
        if self.execution_status != "completed" and self.output_kind != "partial":
            raise ValueError("non-completed execution requires partial output")
        return self

    def slot(self) -> tuple[str, str, int]:
        return self.arm_id, self.case_id, self.repeat_index


def load_dataset(path: Path) -> AgentDataset:
    return AgentDataset.model_validate(load_json(path))


def validate_run_set(
    dataset: AgentDataset, plan: EvaluationPlan, runs: list[RunRecord]
) -> None:
    if canonical_sha256(dataset.model_dump(mode="json")) != plan.dataset_sha256:
        raise ValueError("plan dataset binding mismatch")
    cases = {case.id: case for case in dataset.cases}
    if not set(plan.case_ids) <= cases.keys():
        raise ValueError("plan references unknown cases")
    seen_ids: set[str] = set()
    seen_slots: set[tuple[str, str, int]] = set()
    planned_slots = plan.slots()
    for run in runs:
        if run.run_id in seen_ids or run.slot() in seen_slots:
            raise ValueError("duplicate run or primary slot")
        if run.experiment_id != plan.experiment_id or run.slot() not in planned_slots:
            raise ValueError("unexpected run identity")
        if run.case_sha256 != case_sha256(cases[run.case_id]):
            raise ValueError("run case binding mismatch")
        seen_ids.add(run.run_id)
        seen_slots.add(run.slot())


def write_json(path: Path, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def safe_artifact(root: Path, relative: str) -> Path:
    """Resolve only regular files within a sealed, local artifact directory."""
    reference = Path(relative)
    if reference.is_absolute() or ".." in reference.parts:
        raise ValueError("artifact reference is outside its root")
    target = (root / reference).resolve(strict=True)
    if not target.is_relative_to(root.resolve(strict=True)) or not target.is_file():
        raise ValueError("artifact reference is outside its root")
    return target

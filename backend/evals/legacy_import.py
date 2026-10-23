"""Read-only import of historical Agent results, never a v3 assessment.

Raw bytes remain private and content-bound. Legacy booleans and semantic labels
are preserved as historical signals, not converted into new quality passes.
This command does not import application code, call models, or query live data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, field_validator

from evals.agent_artifacts import MAX_INPUT_BYTES, read_bytes, strict_json, write_json
from evals.agent_schemas import NonBlankText, Sha256, StrictModel, canonical_sha256, sha256_text


class PendingQuality(StrictModel):
    behavior: Literal["pending"] = "pending"
    required_facts: Literal["pending"] = "pending"
    claim_inventory: Literal["pending"] = "pending"
    claim_support: Literal["pending"] = "pending"
    strict_status: Literal["pending"] = "pending"


class ImportedLegacyRun(StrictModel):
    source_position: StrictInt = Field(ge=1)
    question_id: NonBlankText
    run_index: StrictInt = Field(ge=1)
    answer: str
    answer_sha256: Sha256
    legacy_case: dict[str, Any] | None
    legacy_case_sha256: Sha256 | None
    legacy_signals: dict[str, Any]
    new_quality: PendingQuality = Field(default_factory=PendingQuality)
    pending_reasons: list[str]


class ImportedLegacyReview(StrictModel):
    source_sha256: Sha256
    source_position: StrictInt = Field(ge=1)
    question_id: NonBlankText
    run_index: StrictInt | None
    legacy_identity_match: bool
    legacy_labels: dict[str, Any]
    source_metadata: dict[str, Any]
    new_protocol_status: Literal["pending"] = "pending"


class LegacyImportBundle(StrictModel):
    legacy_import_schema_version: Literal[1] = 1
    artifact_kind: Literal["legacy_import_not_assessment"] = "legacy_import_not_assessment"
    source_hashes: dict[str, Sha256]
    legacy_manifest: dict[str, Any]
    legacy_coverage: Literal["complete", "incomplete", "unknown"]
    expected_legacy_runs: StrictInt | None
    runs: list[ImportedLegacyRun]
    reviews: list[ImportedLegacyReview]
    new_protocol_status: Literal["pending"] = "pending"

    @field_validator("legacy_import_schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("legacy import version must be an integer")
        return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value


def _decode(data: bytes) -> Any:
    return strict_json(data.decode("utf-8"))


def _questions(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("legacy questions must be a nonempty JSON array")
    cases: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(payload, start=1):
        case = {"id": f"q{index}", "question": item} if isinstance(item, str) else item
        if not isinstance(case, dict):
            raise ValueError("legacy question must be a string or object")
        identity = _text(case.get("id"), "question id")
        _text(case.get("question"), "question")
        if identity in cases:
            raise ValueError("duplicate legacy question id")
        cases[identity] = case
    return cases


def _case_projection(case: dict[str, Any]) -> dict[str, Any]:
    """Only known v1 defaults; no semantic inference or new atomic facts."""
    result = {
        "kbs": None,
        "required_facts": [],
        "relevant_node_ids": [],
        "relevant_node_groups": [],
        "reference_answer": None,
        "expected_answerable": True,
        "expected_response_phrases": [],
    }
    result.update(case)
    facts = result["required_facts"]
    if not isinstance(facts, list):
        raise ValueError("legacy required_facts must be an array")
    result["required_facts"] = [
        {"aliases": [], **fact} if isinstance(fact, dict) else fact for fact in facts
    ]
    return result


def _import_reviews(
    sources: dict[str, bytes], identities: set[tuple[str, int]]
) -> list[ImportedLegacyReview]:
    imported: list[ImportedLegacyReview] = []
    labels = (
        "answer_correct",
        "required_content_complete",
        "citations_semantically_support",
        "unanswerable_refusal_correct",
    )
    for name, data in sources.items():
        if not name.startswith("review-"):
            continue
        payload = _decode(data)
        if not isinstance(payload, dict):
            raise ValueError("legacy review must be an object")
        version_keys = [
            key for key in ("review_schema_version", "schema_version") if key in payload
        ]
        if len(version_keys) != 1:
            raise ValueError("unsupported or ambiguous legacy review version")
        version = payload[version_keys[0]]
        # One historical review writer used the literal string "1.0". Accept
        # that documented legacy spelling, not generic number/string coercion.
        if not (
            (type(version) is int and version == 1)
            or (version_keys[0] == "schema_version" and version == "1.0")
        ):
            raise ValueError("unsupported legacy review version")
        item_keys = [key for key in ("items", "reviews") if key in payload]
        if len(item_keys) != 1 or not isinstance(payload[item_keys[0]], list):
            raise ValueError("legacy review requires one items/reviews array")
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"items", "reviews", "summary"}
        }
        file_run = payload.get("run_index")
        if file_run is not None:
            _integer(file_run, "review run_index")
        seen: set[tuple[str, int | None]] = set()
        for index, item in enumerate(payload[item_keys[0]], start=1):
            if not isinstance(item, dict):
                raise ValueError("legacy review item must be an object")
            question_id = _text(item.get("question_id"), "review question id")
            run_index = item.get("run_index", file_run)
            if run_index is not None:
                _integer(run_index, "review run_index")
            if file_run is not None and run_index != file_run:
                raise ValueError("review file and item run indices conflict")
            pair = (question_id, run_index)
            if pair in seen:
                raise ValueError("duplicate legacy review identity")
            seen.add(pair)
            imported.append(
                ImportedLegacyReview(
                    source_sha256=hashlib.sha256(data).hexdigest(),
                    source_position=index,
                    question_id=question_id,
                    run_index=run_index,
                    legacy_identity_match=run_index is not None
                    and (question_id, run_index) in identities,
                    legacy_labels={key: item[key] for key in labels if key in item},
                    source_metadata=metadata,
                )
            )
    return imported


def build_import(sources: dict[str, bytes]) -> LegacyImportBundle:
    """Normalize identity links only, using exactly the bytes hashed here."""
    if "results" not in sources or any(
        name not in {"results", "questions"} and not name.startswith("review-") for name in sources
    ):
        raise ValueError("invalid legacy source set")
    if sum(len(data) for data in sources.values()) > MAX_INPUT_BYTES:
        raise ValueError("legacy sources exceed aggregate size limit")
    payload = _decode(sources["results"])
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        raise ValueError("legacy results require a manifest")
    manifest = payload["manifest"]
    version = manifest.get("evaluator_schema_version")
    if version is not None and (type(version) is not int or version not in {1, 2}):
        raise ValueError("unsupported legacy evaluator schema")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("legacy results require an array")
    cases = _questions(_decode(sources["questions"])) if "questions" in sources else {}
    questions_bound = False
    if cases and manifest.get("questions_sha256") is not None:
        expected = manifest["questions_sha256"]
        if expected != hashlib.sha256(sources["questions"]).hexdigest():
            raise ValueError("legacy questions byte hash mismatch")
        questions_bound = True
    count = manifest.get("question_count")
    repeats = manifest.get("runs_per_case")
    if count is not None:
        _integer(count, "question_count")
        if cases and count != len(cases):
            raise ValueError("legacy question count mismatch")
    if repeats is not None:
        _integer(repeats, "runs_per_case")
    expected_runs = count * repeats if count is not None and repeats is not None else None
    imported: list[ImportedLegacyRun] = []
    identities: set[tuple[str, int]] = set()
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError("legacy result must be an object")
        question_id = _text(row.get("question_id"), "result question id")
        run_index = _integer(row.get("run_index"), "result run_index")
        if repeats is not None and run_index > repeats:
            raise ValueError("legacy run_index exceeds manifest")
        if (question_id, run_index) in identities:
            raise ValueError("duplicate legacy run identity")
        identities.add((question_id, run_index))
        if cases and question_id not in cases:
            raise ValueError("result question absent from supplied questions")
        answer = row.get("answer", "")
        if not isinstance(answer, str):
            raise ValueError("legacy answer must be a string")
        legacy_case = row.get("case")
        if legacy_case is not None:
            if not isinstance(legacy_case, dict) or legacy_case.get("id") != question_id:
                raise ValueError("legacy case identity mismatch")
            if cases and canonical_sha256(_case_projection(legacy_case)) != canonical_sha256(
                _case_projection(cases[question_id])
            ):
                raise ValueError("legacy case differs from supplied questions")
        reasons = [
            "LEGACY_SCORER_NOT_V2",
            "V2_GOLD_REQUIRES_REVIEW",
            "HISTORICAL_EVIDENCE_UNVERIFIED",
            "NO_BOUND_V2_REVIEW",
        ]
        if not questions_bound:
            reasons.append("QUESTIONS_SOURCE_UNBOUND")
        if legacy_case is None:
            reasons.append("EMBEDDED_CASE_UNAVAILABLE")
        signals = {
            key: row[key]
            for key in (
                "run_success",
                "execution_success",
                "expected_answerable",
                "response_expectation_met",
                "timed_out",
                "error",
                "quality",
            )
            if key in row
        }
        for key, value in signals.items():
            if key == "quality":
                if not isinstance(value, dict):
                    raise ValueError("legacy quality must be an object")
            elif type(value) is not bool:
                raise ValueError("legacy boolean signal has the wrong type")
        imported.append(
            ImportedLegacyRun(
                source_position=position,
                question_id=question_id,
                run_index=run_index,
                answer=answer,
                answer_sha256=sha256_text(answer),
                legacy_case=legacy_case,
                legacy_case_sha256=canonical_sha256(legacy_case)
                if legacy_case is not None
                else None,
                legacy_signals=signals,
                pending_reasons=reasons,
            )
        )
    if expected_runs is not None and len(imported) > expected_runs:
        raise ValueError("more legacy runs than manifest permits")
    if count is not None and len({item.question_id for item in imported}) > count:
        raise ValueError("more legacy question identities than manifest permits")
    coverage: Literal["complete", "incomplete", "unknown"] = "unknown"
    if expected_runs is not None:
        coverage = "complete" if len(imported) == expected_runs else "incomplete"
    return LegacyImportBundle(
        source_hashes={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
        legacy_manifest=manifest,
        legacy_coverage=coverage,
        expected_legacy_runs=expected_runs,
        runs=imported,
        reviews=_import_reviews(sources, identities),
    )


def import_legacy(
    results: Path,
    output: Path,
    *,
    questions: Path | None = None,
    reviews: list[Path] | None = None,
) -> LegacyImportBundle:
    sources = {"results": read_bytes(results)}

    def add_source(name: str, path: Path) -> None:
        data = read_bytes(path)
        if sum(len(value) for value in sources.values()) + len(data) > MAX_INPUT_BYTES:
            raise ValueError("legacy sources exceed aggregate size limit")
        sources[name] = data

    if questions is not None:
        add_source("questions", questions)
    for index, path in enumerate(reviews or [], start=1):
        add_source(f"review-{index:04d}", path)
    bundle = build_import(sources)
    output.mkdir(parents=True, exist_ok=False)
    private = output / "private"
    private.mkdir()
    originals = private / "sources"
    originals.mkdir()
    for name, data in sources.items():
        (originals / f"{name}.json").write_bytes(data)
    write_json(private / "legacy-import.json", bundle.model_dump(mode="json"))
    public = output / "public"
    public.mkdir()
    # Only fixed categories and counts; no supplied identifiers, labels or text.
    report = (
        "# Legacy import\n\nNot a v3 result or a quality assessment.\n\n"
        f"Imported runs: {len(bundle.runs)}\n\n"
        f"Imported legacy review items: {len(bundle.reviews)}\n\n"
        f"Legacy coverage: {bundle.legacy_coverage}\n\n"
        "New protocol quality: pending; v2 gold, evidence and review must be revalidated.\n"
    )
    (public / "report.md").write_bytes(report.encode("utf-8"))
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--reviews", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = import_legacy(
            args.results, args.output, questions=args.questions, reviews=args.reviews
        )
        print(
            json.dumps(
                {
                    "operation": "legacy_import",
                    "imported_runs": len(bundle.runs),
                    "new_protocol_status": bundle.new_protocol_status,
                }
            )
        )
        return 0  # Import success, deliberately not a quality gate exit code.
    except (OSError, ValueError, RecursionError):
        print("legacy input or import validation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

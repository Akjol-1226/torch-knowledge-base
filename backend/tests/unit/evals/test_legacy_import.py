"""Historical formats are fictional fixtures, not copied business artifacts."""

import copy
import hashlib
import json

import pytest

from evals.agent_artifacts import strict_json
from evals.legacy_import import build_import, import_legacy, main

CANARY = "PRIVATE_FICTIONAL_LEGACY_CANARY"


def sources(*, question_strings=False):
    questions = [CANARY] if question_strings else [{"id": "q1", "question": CANARY}]
    question_bytes = json.dumps(questions).encode()
    case = {"id": "q1", "question": CANARY}
    result = {
        "manifest": {
            "evaluator_schema_version": 2,
            "status": "complete",
            "question_count": 1,
            "runs_per_case": 1,
            "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        },
        "summary": {"successes": 1},
        "results": [
            {
                "question_id": "q1",
                "run_index": 1,
                "case": case,
                "answer": CANARY,
                "run_success": True,
                "execution_success": True,
                "expected_answerable": True,
                "quality": {"required_fact_recall": 1.0, "grounded_answer": True},
            }
        ],
    }
    return {"results": json.dumps(result).encode(), "questions": question_bytes}


def test_import_retains_legacy_success_but_never_upgrades_quality():
    data = sources()
    imported = build_import(data)
    assert imported.artifact_kind == "legacy_import_not_assessment"
    assert imported.legacy_coverage == "complete"
    assert imported.runs[0].legacy_signals["run_success"] is True
    assert imported.runs[0].new_quality.strict_status == "pending"
    assert set(imported.runs[0].new_quality.model_dump().values()) == {"pending"}
    assert imported.source_hashes["results"] == hashlib.sha256(data["results"]).hexdigest()
    assert "QUESTIONS_SOURCE_UNBOUND" not in imported.runs[0].pending_reasons
    assert "HISTORICAL_EVIDENCE_UNVERIFIED" in imported.runs[0].pending_reasons


def test_string_questions_get_only_the_legacy_runner_auto_ids():
    imported = build_import(sources(question_strings=True))
    assert imported.runs[0].question_id == "q1"
    assert imported.new_protocol_status == "pending"


@pytest.mark.parametrize("review_format", ["items", "reviews-with-index", "reviews-file-index"])
def test_all_three_historical_review_shapes_are_preserved_not_reinterpreted(review_format):
    data = sources()
    item = {
        "question_id": "q1",
        "answer_correct": "yes",
        "citations_semantically_support": "yes",
        "notes": CANARY,
    }
    if review_format == "items":
        review = {
            "review_schema_version": 1,
            "run_index": 1,
            "items": [item],
            "review_method": CANARY,
        }
    elif review_format == "reviews-with-index":
        item["run_index"] = 1
        review = {"schema_version": "1.0", "review_scope": CANARY, "reviews": [item]}
    else:
        review = {"schema_version": 1, "run_index": 1, "reviewer": CANARY, "reviews": [item]}
    data["review-0001"] = json.dumps(review).encode()
    imported = build_import(data)
    assert imported.reviews[0].legacy_labels["answer_correct"] == "yes"
    assert imported.reviews[0].legacy_identity_match is True
    assert imported.reviews[0].new_protocol_status == "pending"
    assert imported.runs[0].new_quality.claim_support == "pending"


def test_review_run_index_is_not_guessed_from_filename_or_scope():
    data = sources()
    data["review-0001"] = json.dumps(
        {
            "schema_version": 1,
            "review_scope": "run 1",
            "reviews": [
                {
                    "question_id": "q1",
                    "answer_correct": "yes",
                }
            ],
        }
    ).encode()
    imported = build_import(data)
    assert imported.reviews[0].run_index is None
    assert imported.reviews[0].legacy_identity_match is False


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_only_explicit_historical_review_versions_are_supported(version):
    data = sources()
    data["review-0001"] = json.dumps(
        {
            "schema_version": version,
            "run_index": 1,
            "reviews": [{"question_id": "q1", "answer_correct": "yes"}],
        }
    ).encode()
    with pytest.raises(ValueError, match="review version"):
        build_import(data)


def test_review_file_and_item_identity_conflicts_are_not_guessed_away():
    data = sources()
    data["review-0001"] = json.dumps(
        {
            "schema_version": 1,
            "run_index": 1,
            "reviews": [{"question_id": "q1", "run_index": 2, "answer_correct": "yes"}],
        }
    ).encode()
    with pytest.raises(ValueError, match="indices conflict"):
        build_import(data)


def test_contradictory_review_files_are_retained_without_selecting_a_winner():
    data = sources()
    for index, label in enumerate(("yes", "no"), start=1):
        data[f"review-{index:04d}"] = json.dumps(
            {
                "schema_version": 1,
                "run_index": 1,
                "reviews": [{"question_id": "q1", "answer_correct": label}],
            }
        ).encode()
    imported = build_import(data)
    assert [item.legacy_labels["answer_correct"] for item in imported.reviews] == ["yes", "no"]
    assert imported.runs[0].new_quality.strict_status == "pending"


def test_missing_questions_and_embedded_case_are_explicitly_unbound():
    data = sources()
    del data["questions"]
    result = strict_json(data["results"].decode())
    del result["results"][0]["case"]
    data["results"] = json.dumps(result).encode()
    imported = build_import(data)
    assert imported.runs[0].legacy_case is None
    assert "EMBEDDED_CASE_UNAVAILABLE" in imported.runs[0].pending_reasons
    assert "QUESTIONS_SOURCE_UNBOUND" in imported.runs[0].pending_reasons


def test_missing_run_is_not_hidden_by_manifest_complete():
    data = sources()
    result = strict_json(data["results"].decode())
    result["manifest"]["runs_per_case"] = 2
    data["results"] = json.dumps(result).encode()
    imported = build_import(data)
    assert imported.expected_legacy_runs == 2
    assert imported.legacy_manifest["status"] == "complete"
    assert imported.legacy_coverage == "incomplete"


def test_count_coverage_does_not_accept_more_question_identities_than_the_manifest():
    data = sources()
    data.pop("questions")
    result = strict_json(data["results"].decode())
    result["manifest"]["runs_per_case"] = 2
    other = copy.deepcopy(result["results"][0])
    other.update(question_id="unexpected-q", run_index=2, case=None)
    result["results"].append(other)
    data["results"] = json.dumps(result).encode()
    with pytest.raises(ValueError, match="question identities"):
        build_import(data)


@pytest.mark.parametrize(
    "mutation",
    [
        "questions-hash",
        "case-gold",
        "case-bool",
        "duplicate-run",
        "bool-index",
        "bool-signal",
        "future-version",
    ],
)
def test_identity_and_byte_bindings_cannot_be_silently_repaired(mutation):
    data = sources()
    result = strict_json(data["results"].decode())
    row = result["results"][0]
    if mutation == "questions-hash":
        data["questions"] += b"\n"
    elif mutation == "case-gold":
        row["case"]["required_facts"] = ["wrong fabricated gold"]
    elif mutation == "case-bool":
        row["case"]["expected_answerable"] = 1
    elif mutation == "duplicate-run":
        result["results"].append(copy.deepcopy(row))
    elif mutation == "bool-index":
        row["run_index"] = True
    elif mutation == "bool-signal":
        row["run_success"] = 1
    else:
        result["manifest"]["evaluator_schema_version"] = 3
    data["results"] = json.dumps(result).encode()
    with pytest.raises(ValueError):
        build_import(data)


@pytest.mark.parametrize(
    "bad",
    [
        b'{"manifest":{},"results":[],"results":[]}',
        b'{"manifest":{},"results":[],"x":1e999}',
        b"\xff",
    ],
)
def test_strict_json_is_used_before_import(bad):
    with pytest.raises(ValueError):
        build_import({"results": bad})


def test_aggregate_limit_applies_before_parsing(monkeypatch):
    from evals import legacy_import

    monkeypatch.setattr(legacy_import, "MAX_INPUT_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        build_import(sources())


def test_cli_is_read_only_and_keeps_private_text_out_of_public_and_logs(tmp_path, capsys):
    data = sources()
    paths = {}
    for name, raw in data.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_bytes(raw)
    output = tmp_path / "import"
    args = [
        "--results",
        str(paths["results"]),
        "--questions",
        str(paths["questions"]),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    report = (output / "public" / "report.md").read_bytes()
    assert CANARY.encode() not in report
    assert b"\r\n" not in report
    assert b"Not a v3 result" in report
    for name, raw in data.items():
        assert paths[name].read_bytes() == raw
        assert (output / "private" / "sources" / f"{name}.json").read_bytes() == raw
    assert main(args) == 2
    assert CANARY not in capsys.readouterr().err


def test_cli_error_does_not_echo_proprietary_input(tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"manifest": {}, "results": [{"question_id": CANARY}]}))
    assert main(["--results", str(path), "--output", str(tmp_path / "out")]) == 2
    assert CANARY not in capsys.readouterr().err


def test_import_does_not_query_application_or_model_clients(tmp_path, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("app.", "openai", "langchain", "litellm")):
            raise AssertionError("legacy import accessed live application code")
        return original(name, *args, **kwargs)

    data = sources()
    path = tmp_path / "results.json"
    path.write_bytes(data["results"])
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert import_legacy(path, tmp_path / "out").new_protocol_status == "pending"

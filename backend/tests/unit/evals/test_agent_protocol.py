from __future__ import annotations

import copy
import json
import subprocess
import sys
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from evals.agent_schemas import (
    AgentDataset,
    AgentEvalCase,
    AtomicFact,
    ResultSchemaVersion,
    canonical_json,
    canonical_sha256,
    case_sha256,
    sha256_text,
)


def dataset_payload() -> dict[str, Any]:
    """Entirely fictional, small enough to inspect all label relationships."""
    text = "Example-A at condition C has a maximum permitted temperature of 245 degC."
    return {
        "dataset_schema_version": 2,
        "dataset_id": "fictional-agent-dev",
        "annotation_version": "fictional-r1",
        "cases": [
            {
                "id": "q-fake-001",
                "document_group_id": "fake-template-a",
                "input": {
                    "messages": [{"role": "user", "content": "What is the limit under C?"}],
                    "kbs": ["fake-kb"],
                },
                "expected_behavior": {"allowed_modes": ["answer"]},
                "gold_status": "approved",
                "required_facts": [
                    {
                        "id": "f-limit",
                        "kind": "numeric_constraint",
                        "entity": "Example-A",
                        "attribute": "temperature",
                        "relation": "upper_bound",
                        "value": "245",
                        "unit": "degC",
                        "conditions": ["C"],
                        "document_version": "fake-r1",
                        "evidence_group_ids": ["g-limit"],
                    }
                ],
                "evidence_groups": [{"id": "g-limit", "equivalent_evidence_ids": ["ev-limit"]}],
                "gold_evidence": [
                    {
                        "id": "ev-limit",
                        "handle": "doc_fake:0001",
                        "document_version": "fake-r1",
                        "text": text,
                        "content_sha256": sha256_text(text),
                    }
                ],
            }
        ],
    }


def test_valid_dataset_binds_case_defaults_and_preserves_text():
    payload = dataset_payload()
    dataset = AgentDataset.model_validate(payload)
    case = dataset.cases[0]
    assert case.expected_behavior.allowed_modes == ["answer"]
    assert dataset.scoring_protocol_version == "torch-agent-score/2.0-draft"
    assert case_sha256(case) == canonical_sha256(case.model_dump(mode="json"))
    assert case_sha256(case) != canonical_sha256(payload["cases"][0])
    changed = case.model_dump(mode="json")
    changed["required_facts"][0]["conditions"] = ["different-condition"]
    assert case_sha256(AgentEvalCase.model_validate(changed)) != case_sha256(case)


@pytest.mark.parametrize("version", [True, False, 2.0, "2", 1, 3])
def test_dataset_version_rejects_coercion_and_wrong_versions(version):
    payload = dataset_payload()
    payload["dataset_schema_version"] = version
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)


@pytest.mark.parametrize("version", [True, False, 3.0, "3", 2])
def test_result_version_type_also_requires_exact_integer(version):
    with pytest.raises(ValidationError):
        TypeAdapter(ResultSchemaVersion).validate_python(version)
    assert TypeAdapter(ResultSchemaVersion).validate_python(3) == 3


@pytest.mark.parametrize("identifier", ["", " ", "\t", "q one", " q", "q\x00"])
def test_blank_or_ambiguous_ids_are_rejected(identifier):
    payload = dataset_payload()
    payload["cases"][0]["id"] = identifier
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)


@pytest.mark.parametrize("location", ["envelope", "case", "input", "evidence", "fact"])
def test_unknown_fields_do_not_silently_expand_the_protocol(location):
    payload = dataset_payload()
    case = payload["cases"][0]
    targets = {
        "envelope": payload,
        "case": case,
        "input": case["input"],
        "evidence": case["gold_evidence"][0],
        "fact": case["required_facts"][0],
    }
    targets[location]["unexpected"] = "not supported"
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)


def test_evidence_hash_binds_exact_bytes_not_normalized_or_trimmed_text():
    payload = dataset_payload()
    evidence = payload["cases"][0]["gold_evidence"][0]
    evidence["text"] += "\n"
    with pytest.raises(ValidationError, match="content_sha256"):
        AgentDataset.model_validate(payload)
    evidence["content_sha256"] = sha256_text(evidence["text"])
    assert AgentDataset.model_validate(payload).cases[0].gold_evidence[0].text.endswith("\n")
    assert sha256_text("é") != sha256_text("e\u0301")


@pytest.mark.parametrize("bad_hash", ["A" * 64, "f" * 63, "z" * 64, " " + "f" * 64])
def test_evidence_hash_shape_is_strict(bad_hash):
    payload = dataset_payload()
    payload["cases"][0]["gold_evidence"][0]["content_sha256"] = bad_hash
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)


def test_case_fact_group_and_evidence_identities_must_be_unique():
    for field in ("required_facts", "evidence_groups", "gold_evidence"):
        payload = dataset_payload()
        case = payload["cases"][0]
        case[field].append(copy.deepcopy(case[field][0]))
        with pytest.raises(ValidationError, match="unique"):
            AgentDataset.model_validate(payload)
    payload = dataset_payload()
    payload["cases"].append(copy.deepcopy(payload["cases"][0]))
    with pytest.raises(ValidationError, match="case IDs"):
        AgentDataset.model_validate(payload)


@pytest.mark.parametrize("target", ["group", "fact"])
def test_dangling_references_are_rejected(target):
    payload = dataset_payload()
    case = payload["cases"][0]
    if target == "group":
        case["evidence_groups"][0]["equivalent_evidence_ids"] = ["missing-evidence"]
    else:
        case["required_facts"][0]["evidence_group_ids"] = ["missing-group"]
    with pytest.raises(ValidationError, match="unknown evidence"):
        AgentDataset.model_validate(payload)


def test_evidence_alternatives_are_nonempty_unique_and_disjoint():
    payload = dataset_payload()
    group = payload["cases"][0]["evidence_groups"][0]
    group["equivalent_evidence_ids"] = []
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)
    group["equivalent_evidence_ids"] = ["ev-limit", "ev-limit"]
    with pytest.raises(ValidationError, match="unique"):
        AgentDataset.model_validate(payload)
    group["equivalent_evidence_ids"] = ["ev-limit"]
    payload["cases"][0]["evidence_groups"].append(
        {"id": "g-second", "equivalent_evidence_ids": ["ev-limit"]}
    )
    with pytest.raises(ValidationError, match="disjoint"):
        AgentDataset.model_validate(payload)


def test_any_of_alternatives_and_shared_fact_group_are_representable():
    payload = dataset_payload()
    case = payload["cases"][0]
    alternate = copy.deepcopy(case["gold_evidence"][0])
    alternate.update(id="ev-alternate", handle="doc_fake:0002")
    case["gold_evidence"].append(alternate)
    case["evidence_groups"][0]["equivalent_evidence_ids"].append("ev-alternate")
    second_fact = copy.deepcopy(case["required_facts"][0])
    second_fact["id"] = "f-another"
    case["required_facts"].append(second_fact)
    result = AgentDataset.model_validate(payload)
    assert result.cases[0].evidence_groups[0].equivalent_evidence_ids == [
        "ev-limit",
        "ev-alternate",
    ]
    # The schema verifies OR structure, not the semantic equivalence annotation.
    assert result.cases[0].required_facts[1].evidence_group_ids == ["g-limit"]


def test_fact_source_revision_cannot_silently_follow_a_different_version():
    payload = dataset_payload()
    payload["cases"][0]["required_facts"][0]["document_version"] = "fake-r2"
    with pytest.raises(ValidationError, match="versions differ"):
        AgentDataset.model_validate(payload)


def test_evidence_id_and_node_content_are_consistent_across_cases():
    payload = dataset_payload()
    other = copy.deepcopy(payload["cases"][0])
    other["id"] = "q-fake-002"
    payload["cases"].append(other)
    assert len(AgentDataset.model_validate(payload).cases) == 2
    other["gold_evidence"][0]["text"] = "A conflicting fictional source."
    other["gold_evidence"][0]["content_sha256"] = sha256_text(other["gold_evidence"][0]["text"])
    with pytest.raises(ValidationError, match="inconsistent content"):
        AgentDataset.model_validate(payload)
    # Renaming the ID cannot launder a different full snapshot for the same node.
    other["gold_evidence"][0]["id"] = "ev-renamed"
    other["evidence_groups"][0]["equivalent_evidence_ids"] = ["ev-renamed"]
    with pytest.raises(ValidationError, match="inconsistent snapshot"):
        AgentDataset.model_validate(payload)


@pytest.mark.parametrize("mutation", ["two-messages", "system", "two-modes", "clarify-no-slots"])
def test_first_version_is_single_user_turn_and_single_explicit_mode(mutation):
    payload = dataset_payload()
    case = payload["cases"][0]
    if mutation == "two-messages":
        case["input"]["messages"].append({"role": "user", "content": "next turn"})
    elif mutation == "system":
        case["input"]["messages"][0]["role"] = "system"
    elif mutation == "two-modes":
        case["expected_behavior"]["allowed_modes"] = ["refuse", "clarify"]
    else:
        case["expected_behavior"]["allowed_modes"] = ["clarify"]
    with pytest.raises(ValidationError):
        AgentDataset.model_validate(payload)


def test_refusal_can_have_boundary_evidence_but_not_implicit_missing_slots():
    payload = dataset_payload()
    case = payload["cases"][0]
    case["expected_behavior"]["allowed_modes"] = ["refuse"]
    case["required_facts"] = []
    assert AgentDataset.model_validate(payload).cases[0].gold_evidence
    case["expected_behavior"]["missing_slots"] = ["model"]
    with pytest.raises(ValidationError, match="only applies"):
        AgentDataset.model_validate(payload)


def test_draft_labels_do_not_become_approved_merely_by_parsing():
    payload = dataset_payload()
    case = payload["cases"][0]
    case["required_facts"] = []
    with pytest.raises(ValidationError, match="approved answer"):
        AgentDataset.model_validate(payload)
    del case["gold_status"]
    assert AgentDataset.model_validate(payload).cases[0].gold_status == "draft"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e3", "245 degC", "", "1_000", 245, True])
def test_numeric_facts_require_plain_finite_decimal_strings(value):
    fact = dataset_payload()["cases"][0]["required_facts"][0]
    fact["value"] = value
    with pytest.raises(ValidationError):
        AtomicFact.model_validate(fact)


@pytest.mark.parametrize("mutation", ["missing-unit", "blank-unit", "requires", "numeric-alias"])
def test_kind_relation_and_unit_contracts_are_not_silently_coerced(mutation):
    fact = dataset_payload()["cases"][0]["required_facts"][0]
    if mutation == "missing-unit":
        fact.pop("unit")
    elif mutation == "blank-unit":
        fact["unit"] = " "
    elif mutation == "requires":
        fact["relation"] = "requires"
    else:
        fact["aliases"] = ["473 K"]
    with pytest.raises(ValidationError):
        AtomicFact.model_validate(fact)
    fact.update(kind="text", relation="equal", value="some policy", unit=None, aliases=[])
    assert AtomicFact.model_validate(fact).value == "some policy"
    fact["unit"] = "degC"
    with pytest.raises(ValidationError, match="no numeric fields"):
        AtomicFact.model_validate(fact)


@pytest.mark.parametrize(
    ("lower", "upper", "lower_inclusive", "upper_inclusive"),
    [("24", "18", True, True), ("18", "18", False, True), ("18", "24", None, True)],
)
def test_interval_requires_order_and_explicit_nonempty_boundary_semantics(
    lower, upper, lower_inclusive, upper_inclusive
):
    fact = dataset_payload()["cases"][0]["required_facts"][0]
    fact.update(
        relation="interval",
        value=None,
        lower=lower,
        upper=upper,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
    )
    with pytest.raises(ValidationError):
        AtomicFact.model_validate(fact)
    fact.update(lower="18", upper="24", lower_inclusive=True, upper_inclusive=True)
    assert AtomicFact.model_validate(fact).relation == "interval"


def test_hashes_are_stable_without_laundering_non_json_types():
    value = {"z": "é", "a": ["x", 3]}
    assert canonical_json(value) == '{"a":["x",3],"z":"é"}'
    assert canonical_sha256(value) == canonical_sha256({"a": ["x", 3], "z": "é"})
    assert sha256_text("x") != sha256_text("x\n")
    assert json.loads(canonical_json(value)) == value


@pytest.mark.parametrize(
    "value", [float("nan"), {"x": float("inf")}, {1: "value"}, (1, 2), {1}, Decimal("1")]
)
def test_canonical_hash_rejects_non_json_and_nonfinite_values(value):
    with pytest.raises(ValueError):
        canonical_json(value)


def test_import_does_not_load_application_runtime_or_model_clients():
    result = subprocess.run(  # noqa: S603 - fixed interpreter and literal offline import probe.
        [
            sys.executable,
            "-c",
            "import sys; import evals.agent_schemas; "
            "assert not any(n == 'app' or n.startswith('app.') for n in sys.modules); "
            "assert 'langchain_openai' not in sys.modules; assert 'httpx' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

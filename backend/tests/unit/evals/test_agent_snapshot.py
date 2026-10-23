"""Invented staging fixtures; receipt fixtures do not claim real Windows leases."""

from __future__ import annotations

import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from evals.agent_schemas import canonical_json, canonical_sha256
from evals.agent_snapshot import PROFILE, audit_release, create_release, load_release


def encoded(value):
    return (canonical_json(value) + "\n").encode()


def receipt(staging):
    previous = staging / "source-capture.json"
    payload = (
        json.loads(previous.read_bytes())
        if previous.exists()
        else {
            "source_capture_schema_version": 1,
            "capture_method": "windows_read_handles_deny_write_delete",
            "all_handles_held_during_read": True,
            "source_data_dir": str(staging.parent / "fictional-live-data-never-read"),
        }
    )
    payload["files"] = {
        path.relative_to(staging).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in staging.rglob("*")
        if path.is_file() and path != previous
    }
    previous.write_bytes(encoded(payload))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def stage(root):
    staging = root / "staging"
    markdown = "# Fictional manual\n\n## Dry limit\nThe dry limit is 42.\n\n## Startup\nStartup is 18 to 24.\n"
    doc = {
        "id": "doc_fictional",
        "type": "md",
        "kb": "fictional-only",
        "path": str(root / "fictional-live-data-never-read" / "md" / "fixture" / "manual.md"),
        "doc_name": "Fictional manual",
        "doc_description": "Invented test material.",
        "line_count": len(markdown.split("\n")),
        "structure": [
            {
                "node_id": "0001",
                "title": "Fictional manual",
                "text": "# Fictional manual",
                "line_num": 1,
                "nodes": [
                    {
                        "node_id": "0002",
                        "title": "Dry limit",
                        "summary": "Fictional dry limit.",
                        "text": "## Dry limit\nThe dry limit is 42.",
                        "line_num": 3,
                    },
                    {
                        "node_id": "0003",
                        "title": "Startup",
                        "summary": "Fictional startup.",
                        "text": "## Startup\nStartup is 18 to 24.",
                        "line_num": 6,
                    },
                ],
            }
        ],
    }
    write_json(staging / "workspace" / "doc_fictional.json", doc)
    write_json(
        staging / "workspace" / "_meta.json",
        {
            doc["id"]: {
                key: doc[key]
                for key in ("type", "kb", "path", "doc_name", "doc_description", "line_count")
            }
        },
    )
    write_json(
        staging / "catalog" / "document_catalog.json",
        [
            {
                "doc_id": doc["id"],
                "kb": doc["kb"],
                "doc_name": doc["doc_name"],
                "doc_description": doc["doc_description"],
            }
        ],
    )
    md = staging / "md" / "fixture" / "manual.md"
    md.parent.mkdir(parents=True)
    md.write_bytes(markdown.encode())
    (staging / "domain_dict_auto.txt").write_bytes(b"Fictional\t10\tn\n")
    receipt(staging)
    return staging


@pytest.fixture(scope="module")
def release_base(tmp_path_factory):
    root = tmp_path_factory.mktemp("fictional-release")
    staging = stage(root)
    output = root / "release"
    create_release(staging, output, retrieval_top_n=50)
    return output


def copied_release(tmp_path, release_base):
    output = tmp_path / "release"
    shutil.copytree(release_base, output)
    return output


def reseal(output):
    path = output / "manifest.json"
    payload = json.loads(path.read_bytes())
    payload["files"] = {
        item.relative_to(output).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in output.rglob("*")
        if item.is_file() and item != path
    }
    payload.pop("release_id")
    payload["release_id"] = canonical_sha256(payload)
    path.write_bytes(encoded(payload))


def test_release_is_explicit_rebuilt_bm25_and_every_file_is_content_bound(release_base):
    manifest = audit_release(release_base)
    assert manifest["retrieval_profile"] == PROFILE
    assert manifest["historical_indexes"] == "discarded_not_reused_or_validated"
    assert manifest["vector_policy"] == "disabled_for_both_arms"
    assert manifest["documents"] == 1 and manifest["nodes"] == 3
    assert set(manifest["files"]) == {
        path.relative_to(release_base).as_posix()
        for path in release_base.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    workspace = json.loads((release_base / "data/workspace/doc_fictional.json").read_bytes())
    assert workspace["path"] == "md/fixture/manual.md"


def test_loaded_reader_uses_verified_memory_after_all_files_are_removed(
    tmp_path,
    release_base,
    monkeypatch,
):
    from app.core.retrieval import treestore

    output = copied_release(tmp_path, release_base)
    reader = load_release(output)
    monkeypatch.setattr(treestore, "get_settings", lambda: SimpleNamespace(retrieval_top_n=50))
    shutil.rmtree(output)
    assert reader.read_node("doc_fictional:0002")["text"] == "## Dry limit\nThe dry limit is 42."
    assert reader.search_nodes("dry limit", top_k=1)[0]["id"] == "doc_fictional:0002"
    assert reader.list_catalog(kbs=["other"]) == []
    assert not hasattr(reader, "incremental_clone")
    catalog = reader.list_catalog()
    catalog[0]["doc_name"] = "Mutated caller copy"
    assert reader.list_catalog()[0]["doc_name"] == "Fictional manual"


def test_query_configuration_cannot_silently_change_the_release(release_base, monkeypatch):
    from app.core.retrieval import treestore

    reader = load_release(release_base)
    monkeypatch.setattr(treestore, "get_settings", lambda: SimpleNamespace(retrieval_top_n=999))
    with pytest.raises(ValueError, match="retrieval_top_n"):
        reader.search_nodes("dry")


@pytest.mark.parametrize(
    "mutation",
    [
        "md",
        "node-body",
        "node-title",
        "node-parent",
        "line",
        "line-count",
        "catalog",
        "meta",
        "duplicate-node",
        "missing-md",
        "orphan-md",
        "escape",
        "summary-type",
    ],
)
def test_internally_inconsistent_source_is_rejected_even_with_fresh_receipt_hashes(
    tmp_path, mutation
):
    staging = stage(tmp_path)
    workspace = staging / "workspace/doc_fictional.json"
    doc = json.loads(workspace.read_bytes())
    root = doc["structure"][0]
    if mutation == "md":
        (staging / "md/fixture/manual.md").write_bytes(b"# A different document\n")
    elif mutation == "node-body":
        root["nodes"][0]["text"] += "\nFabricated body."
    elif mutation == "node-title":
        root["nodes"][0]["title"] = "Different title"
    elif mutation == "node-parent":
        doc["structure"] = [root, *root.pop("nodes")]
    elif mutation == "line":
        root["nodes"][0]["line_num"] = True
    elif mutation == "line-count":
        doc["line_count"] += 1
    elif mutation == "catalog":
        write_json(staging / "catalog/document_catalog.json", [])
    elif mutation == "meta":
        write_json(staging / "workspace/_meta.json", {})
    elif mutation == "duplicate-node":
        root["nodes"][1]["node_id"] = "0002"
    elif mutation == "missing-md":
        (staging / "md/fixture/manual.md").unlink()
    elif mutation == "orphan-md":
        (staging / "md/fixture/orphan.md").write_bytes(b"# Orphan\n")
    elif mutation == "escape":
        doc["path"] = str(tmp_path / "outside" / "manual.md")
    else:
        root["nodes"][0]["summary"] = False
    write_json(workspace, doc)
    receipt(staging)
    with pytest.raises(ValueError):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)
    assert not (tmp_path / "release").exists()


@pytest.mark.parametrize(
    "mutation", ["wrong-method", "unheld", "bool-version", "changed", "extra", "old-index"]
)
def test_source_receipt_and_exact_file_set_are_required(tmp_path, mutation):
    staging = stage(tmp_path)
    path = staging / "source-capture.json"
    payload = json.loads(path.read_bytes())
    if mutation == "wrong-method":
        payload["capture_method"] = "before_after_live_directory_hash"
    elif mutation == "unheld":
        payload["all_handles_held_during_read"] = 1
    elif mutation == "bool-version":
        payload["source_capture_schema_version"] = True
    elif mutation == "changed":
        (staging / "domain_dict_auto.txt").write_bytes(b"changed")
    elif mutation == "extra":
        (staging / "extra.txt").write_bytes(b"undeclared")
    else:
        write_json(staging / "indexes/meta.json", [{"node_id_full": "stale"}])
        receipt(staging)
        payload = json.loads(path.read_bytes())
    path.write_bytes(encoded(payload))
    with pytest.raises(ValueError):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)


@pytest.mark.parametrize(
    "mutation", ["raw-file", "extra-file", "rehashed-node", "rehashed-index", "bool-count"]
)
def test_release_tampering_cannot_pass_just_by_rehashing_the_manifest(
    tmp_path, release_base, mutation
):
    output = copied_release(tmp_path, release_base)
    if mutation == "raw-file":
        (output / "source/domain_dict_auto.txt").write_bytes(b"changed")
    elif mutation == "extra-file":
        (output / "data/indexes/embeddings.npy").write_bytes(b"vector not allowed")
        reseal(output)
    elif mutation == "rehashed-node":
        path = output / "data/workspace/doc_fictional.json"
        doc = json.loads(path.read_bytes())
        doc["structure"][0]["nodes"][0]["text"] = "Forged answer."
        path.write_bytes(encoded(doc))
        reseal(output)
    elif mutation == "rehashed-index":
        path = output / "data/indexes/bm25/data.csc.index.npy"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        reseal(output)
    else:
        path = output / "manifest.json"
        payload = json.loads(path.read_bytes())
        payload["documents"] = True
        payload.pop("release_id")
        payload["release_id"] = canonical_sha256(payload)
        path.write_bytes(encoded(payload))
    with pytest.raises(ValueError):
        audit_release(output)


def test_repeated_creation_is_deterministic_and_existing_output_is_rejected(tmp_path):
    staging = stage(tmp_path)
    first = create_release(staging, tmp_path / "one", retrieval_top_n=50)
    second = create_release(staging, tmp_path / "two", retrieval_top_n=50)
    assert first == second
    with pytest.raises(FileExistsError):
        create_release(staging, tmp_path / "one", retrieval_top_n=50)


def test_known_thinning_blank_separators_are_allowed_without_changing_nonblank_content(tmp_path):
    staging = stage(tmp_path)
    path = staging / "workspace/doc_fictional.json"
    doc = json.loads(path.read_bytes())
    root = doc["structure"][0]
    root["text"] += "\n\n" + "\n\n".join(child["text"] for child in root.pop("nodes"))
    path.write_bytes(encoded(doc))
    receipt(staging)
    manifest = create_release(staging, tmp_path / "release", retrieval_top_n=50)
    assert manifest["nodes"] == 1


def test_global_jieba_additions_cannot_change_the_frozen_tokenizer_or_rebuild(release_base):
    import jieba

    jieba.add_word("GLOBAL_ONLY_NEVER_FROZEN_TOKEN", freq=10000000)
    reader = load_release(release_base)
    assert reader.release_id == audit_release(release_base)["release_id"]


def test_linked_staging_file_is_rejected(tmp_path):
    staging = stage(tmp_path)
    target = staging / "domain_dict_auto.txt"
    alias = staging / "md/fixture/alias.txt"
    try:
        alias.hardlink_to(target)
    except OSError:
        pytest.skip("hardlink creation unavailable")
    with pytest.raises(ValueError, match="unlinked"):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)


def test_no_model_or_embedding_client_is_accessed(tmp_path, monkeypatch):
    from app.core.retrieval import treestore

    def forbidden():
        raise AssertionError("network client must not be constructed")

    monkeypatch.setattr(treestore, "get_embed_client", forbidden)
    staging = stage(tmp_path)
    output = tmp_path / "release"
    create_release(staging, output, retrieval_top_n=50)
    reader = load_release(output)
    monkeypatch.setattr(treestore, "get_settings", lambda: SimpleNamespace(retrieval_top_n=50))
    assert reader.search_nodes("dry", top_k=1)


def test_changed_retrieval_code_identity_is_rejected_even_after_manifest_reseal(
    tmp_path, release_base
):
    output = copied_release(tmp_path, release_base)
    path = output / "manifest.json"
    payload = json.loads(path.read_bytes())
    payload["implementation"] = {"a-different-reader.py": "0" * 64}
    payload.pop("release_id")
    payload["release_id"] = canonical_sha256(payload)
    path.write_bytes(encoded(payload))
    with pytest.raises(ValueError, match="unsupported release profile or software"):
        load_release(output)


def test_global_hmm_force_split_drift_is_rejected(tmp_path, monkeypatch):
    import jieba.finalseg

    staging = stage(tmp_path)
    monkeypatch.setattr(jieba.finalseg, "Force_Split_Words", {"a-global-split-rule"})
    with pytest.raises(ValueError, match="HMM splitting state"):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)


def test_generated_release_limits_apply_before_output_is_created(tmp_path, monkeypatch):
    from evals import agent_snapshot

    staging = stage(tmp_path)
    source_files = sum(path.is_file() for path in staging.rglob("*"))
    monkeypatch.setattr(agent_snapshot, "MAX_FILES", source_files + 1)
    with pytest.raises(ValueError, match="generated release exceeds"):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)
    assert not (tmp_path / "release").exists()


def test_receipt_cannot_smuggle_non_md_payload_under_the_md_directory(tmp_path):
    staging = stage(tmp_path)
    (staging / "md/fixture/private.env").write_bytes(b"FICTIONAL_SECRET_CANARY")
    receipt(staging)
    with pytest.raises(ValueError, match="unexpected source file"):
        create_release(staging, tmp_path / "release", retrieval_top_n=50)


@pytest.mark.parametrize("which", ["staging", "output"])
def test_parent_traversal_in_root_paths_is_rejected_before_overlap_checks(tmp_path, which):
    staging = stage(tmp_path)
    output = tmp_path / "release"
    if which == "staging":
        staging = staging / "unused" / ".."
    else:
        output = staging / "unused" / ".." / "nested-release"
    with pytest.raises(ValueError, match="must not traverse parents"):
        create_release(staging, output, retrieval_top_n=50)


@pytest.mark.parametrize(
    "prefix,allowed", [("\n\n", True), ("An unindexed precondition.\n", False)]
)
def test_nonblank_md_preamble_cannot_disappear_outside_all_tree_nodes(tmp_path, prefix, allowed):
    staging = stage(tmp_path)
    md = staging / "md/fixture/manual.md"
    md.write_bytes(prefix.encode() + md.read_bytes())
    path = staging / "workspace/doc_fictional.json"
    doc = json.loads(path.read_bytes())
    shift = prefix.count("\n")
    doc["line_count"] += shift
    for node in [doc["structure"][0], *doc["structure"][0]["nodes"]]:
        node["line_num"] += shift
    path.write_bytes(encoded(doc))
    meta_path = staging / "workspace/_meta.json"
    meta = json.loads(meta_path.read_bytes())
    meta[doc["id"]]["line_count"] += shift
    meta_path.write_bytes(encoded(meta))
    receipt(staging)
    if allowed:
        assert create_release(staging, tmp_path / "release", retrieval_top_n=50)["nodes"] == 3
    else:
        with pytest.raises(ValueError, match="preamble has no indexed node"):
            create_release(staging, tmp_path / "release", retrieval_top_n=50)

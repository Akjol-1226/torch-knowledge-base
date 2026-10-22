import json
import os
from contextlib import contextmanager

import pytest

from evals import agent_source_capture as capture


def _source(tmp_path):
    root = tmp_path / "data"
    (root / "catalog").mkdir(parents=True)
    (root / "workspace").mkdir()
    (root / "md").mkdir()
    (root / "indexes").mkdir()
    (root / "indexes" / "not-captured.txt").write_text("old index", encoding="utf-8")
    (root / "catalog" / "document_catalog.json").write_text(
        json.dumps([{"doc_id": "doc_fiction"}]), encoding="utf-8"
    )
    (root / "workspace" / "doc_fiction.json").write_text(json.dumps({
        "id": "doc_fiction", "path": str(root / "md" / "fiction.md"), "structure": [],
    }), encoding="utf-8")
    (root / "workspace" / "_meta.json").write_text("{}", encoding="utf-8")
    (root / "md" / "fiction.md").write_text("Fictional source", encoding="utf-8")
    (root / "domain_dict_auto.txt").write_text("Fictional 10", encoding="utf-8")
    return root


@pytest.fixture
def fake_leases(monkeypatch):
    active = set()

    @contextmanager
    def lease(path):
        active.add(path)
        try:
            with path.open("rb") as stream:
                yield stream
        finally:
            active.remove(path)

    monkeypatch.setattr(capture, "windows_read_handle", lease)
    return active


def test_every_source_handle_is_still_held_when_receipt_is_written(tmp_path, fake_leases, monkeypatch):
    root = _source(tmp_path)
    original = capture.write_json

    def write(path, manifest):
        assert len(fake_leases) == 5
        assert len(manifest["files"]) == 5
        original(path, manifest)

    monkeypatch.setattr(capture, "write_json", write)
    result = capture.capture_source(root, tmp_path / "staging")
    assert result["all_handles_held_during_read"] is True
    assert fake_leases == set()
    assert "md/fiction.md" in result["files"]
    assert not any(name.startswith("indexes/") for name in result["files"])
    with pytest.raises(ValueError, match="new"):
        capture.capture_source(root, tmp_path / "staging")


def test_source_escape_does_not_publish_partial_receipt(tmp_path, fake_leases):
    root = _source(tmp_path)
    (root / "workspace" / "doc_fiction.json").write_text(json.dumps({
        "id": "doc_fiction", "path": str(tmp_path / "outside.md"),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        capture.capture_source(root, tmp_path / "staging")
    assert fake_leases == set()
    assert not (tmp_path / "staging").exists()


def test_missing_catalog_member_rejects_capture(tmp_path, fake_leases):
    root = _source(tmp_path)
    (root / "catalog" / "document_catalog.json").write_text(
        '[{"doc_id":"doc_missing"}]', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="membership"):
        capture.capture_source(root, tmp_path / "staging")
    assert not (tmp_path / "staging").exists()


def test_workspace_filename_and_declared_identity_must_match(tmp_path, fake_leases):
    root = _source(tmp_path)
    (root / "workspace" / "doc_fiction.json").rename(root / "workspace" / "doc_other.json")
    with pytest.raises(ValueError, match="membership"):
        capture.capture_source(root, tmp_path / "staging")
    assert not (tmp_path / "staging").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_real_windows_read_lease_prevents_writes_and_deletes(tmp_path):
    path = tmp_path / "lease.txt"
    path.write_text("original", encoding="utf-8")
    with capture.windows_read_handle(path) as stream:
        assert stream.read() == b"original"
        with pytest.raises(OSError):
            path.write_text("modified", encoding="utf-8")
        with pytest.raises(OSError):
            path.unlink()
    path.write_text("after lease", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "after lease"


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_real_capture_refuses_an_already_open_writer(tmp_path):
    root = _source(tmp_path)
    path = root / "md" / "fiction.md"
    with path.open("r+b"):
        with pytest.raises(ValueError, match="lease"):
            capture.capture_source(root, tmp_path / "staging")
    assert not (tmp_path / "staging").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_fdopen_failure_releases_the_owned_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "fdopen.txt"
    path.write_text("original", encoding="utf-8")

    def failure(*args, **kwargs):
        raise OSError("injected fdopen failure")

    with monkeypatch.context() as patch:
        patch.setattr(capture.os, "fdopen", failure)
        with pytest.raises(OSError, match="injected"):
            with capture.windows_read_handle(path):
                pass
    path.write_text("write after failure", encoding="utf-8")

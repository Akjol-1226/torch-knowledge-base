from datetime import UTC, datetime

import pytest

from app.modules.docgen import storage
from app.modules.docgen.schemas import BlueprintItem, DocgenSessionRecord, DocgenStatus


def _record(session_id="b1_20260702_abcdef"):
    now = datetime(2026, 7, 2, tzinfo=UTC)
    return DocgenSessionRecord(
        session_id=session_id,
        doc_id="doc_a",
        status=DocgenStatus.waiting_for_requirement,
        blueprint=BlueprintItem(doc_id="doc_a", doc_name="工艺文件"),
        summary="蓝本摘要",
        outline=[],
        question="你想基于它做份什么新文档？",
        created_at=now,
        updated_at=now,
    )


def test_save_and_load_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )
    rec = _record()

    storage.save_session(rec)
    loaded = storage.load_session(rec.session_id)

    assert loaded is not None
    assert loaded.session_id == rec.session_id
    assert loaded.summary == "蓝本摘要"


def test_update_session_locks_read_modify_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )
    rec = _record()
    storage.save_session(rec)

    def mutate(item):
        item.summary = "更新后的摘要"
        return item

    updated = storage.update_session(rec.session_id, mutate)
    loaded = storage.load_session(rec.session_id)

    assert updated is not None
    assert loaded is not None
    assert loaded.summary == "更新后的摘要"


def test_load_unknown_session_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert storage.load_session("missing") is None


def test_rejects_unsafe_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    with pytest.raises(ValueError):
        storage.load_session("../bad")

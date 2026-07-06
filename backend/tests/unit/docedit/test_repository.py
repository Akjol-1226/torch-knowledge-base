from __future__ import annotations

from app.modules.docedit.models import DocumentChange  # noqa: F401
from app.modules.docedit.repository import DoceditRepository


async def test_get_or_create_document_state_initializes_version_one(db_session) -> None:
    repo = DoceditRepository(db_session)

    state = await repo.get_or_create_document_state("doc_a")

    assert state.doc_id == "doc_a"
    assert state.current_version == 1
    assert state.summary_status == "clean"


async def test_bump_document_version_updates_current_version(db_session) -> None:
    repo = DoceditRepository(db_session)
    await repo.get_or_create_document_state("doc_a")

    state = await repo.bump_document_version("doc_a")

    assert state.current_version == 2


async def test_create_change_appends_history_newest_first(db_session) -> None:
    repo = DoceditRepository(db_session)
    await repo.get_or_create_document_state("doc_a")
    first = await repo.create_change(
        doc_id="doc_a",
        version_from=1,
        version_to=2,
        node_handle="doc_a:0001",
        node_title="A",
        node_path="Doc > A",
        op_type="manual_edit",
        actor_user_id=1,
        actor_username="admin",
        before_text="old",
        after_text="new",
        before_hash="sha256:a",
        after_hash="sha256:b",
        diff_html="<del>old</del><ins>new</ins>",
    )
    second = await repo.create_change(
        doc_id="doc_a",
        version_from=2,
        version_to=3,
        node_handle="doc_a:0001",
        node_title="A",
        node_path="Doc > A",
        op_type="manual_edit",
        actor_user_id=1,
        actor_username="admin",
        before_text="new",
        after_text="newer",
        before_hash="sha256:b",
        after_hash="sha256:c",
        diff_html="<del>new</del><ins>newer</ins>",
    )

    history = await repo.list_changes("doc_a")

    assert [item.id for item in history] == [second.id, first.id]


async def test_upsert_node_edit_state_tracks_changed_flag(db_session) -> None:
    repo = DoceditRepository(db_session)

    changed = await repo.upsert_node_edit_state(
        doc_id="doc_a",
        node_handle="doc_a:0001",
        original_hash="sha256:original",
        current_hash="sha256:new",
        last_change_id=1,
    )
    restored = await repo.upsert_node_edit_state(
        doc_id="doc_a",
        node_handle="doc_a:0001",
        original_hash="sha256:original",
        current_hash="sha256:original",
        last_change_id=2,
    )

    assert changed.changed is True
    assert restored.changed is False


async def test_rollback_history_is_append_only(db_session) -> None:
    repo = DoceditRepository(db_session)
    await repo.get_or_create_document_state("doc_a")
    source = await repo.create_change(
        doc_id="doc_a",
        version_from=1,
        version_to=2,
        node_handle="doc_a:0001",
        node_title="A",
        node_path="Doc > A",
        op_type="manual_edit",
        actor_user_id=1,
        actor_username="admin",
        before_text="old",
        after_text="new",
        before_hash="sha256:a",
        after_hash="sha256:b",
        diff_html="diff",
    )
    rollback = await repo.create_change(
        doc_id="doc_a",
        version_from=2,
        version_to=3,
        node_handle="doc_a:0001",
        node_title="A",
        node_path="Doc > A",
        op_type="rollback",
        actor_user_id=1,
        actor_username="admin",
        before_text="new",
        after_text="old",
        before_hash="sha256:b",
        after_hash="sha256:a",
        diff_html="diff",
        source_change_id=source.id,
    )

    history = await repo.list_changes("doc_a")

    assert len(history) == 2
    assert history[0].id == rollback.id
    assert history[1].id == source.id

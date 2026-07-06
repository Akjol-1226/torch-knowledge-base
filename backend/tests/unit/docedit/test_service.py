from __future__ import annotations

import pytest

from app.modules.auth.dependencies import CurrentUser
from app.modules.docedit.diff import text_hash
from app.modules.docedit.schemas import NodeSaveRequest
from app.modules.docedit.service import (
    DoceditConflict,
    DoceditForbidden,
    DoceditService,
    DoceditUnprocessableEntity,
)


class FakeRepo:
    def __init__(self) -> None:
        self.version = 1
        self.changes = []
        self.node_states = {}
        self.active = False

    async def get_or_create_document_state(self, doc_id: str):
        return type("State", (), {"doc_id": doc_id, "current_version": self.version, "summary_status": "clean"})()

    async def list_changed_node_handles(self, doc_id: str):
        return list(self.node_states)

    async def has_active_reparse(self, doc_id: str) -> bool:
        return self.active

    async def get_node_state(self, handle: str):
        return self.node_states.get(handle)

    async def bump_document_version(self, doc_id: str):
        self.version += 1
        return type("State", (), {"doc_id": doc_id, "current_version": self.version, "summary_status": "stale"})()

    async def create_change(self, **kwargs):
        change = type("Change", (), {"id": len(self.changes) + 1, **kwargs})()
        self.changes.append(change)
        return change

    async def upsert_node_edit_state(self, **kwargs):
        state = type("NodeState", (), {"changed": kwargs["current_hash"] != kwargs["original_hash"], **kwargs})()
        self.node_states[kwargs["node_handle"]] = state
        return state


class FakeWorkspace:
    def __init__(self) -> None:
        self.body = "old"
        self.published = []

    def read_editable_node(self, handle: str, *, allow_merged_member: bool = False):
        return {
            "handle": handle,
            "doc_id": "doc_sample",
            "node_id": "0001",
            "title": "A",
            "path": "Sample > A",
            "text": self.body,
            "text_hash": text_hash(self.body),
            "has_children": False,
            "has_text": True,
            "editable": True,
            "editable_reason": None,
        }

    def publish(
        self,
        doc_id: str,
        node_id: str,
        base_hash: str,
        after_text: str,
        *,
        allow_merged_member: bool = False,
    ):
        if base_hash != text_hash(self.body):
            raise DoceditConflict("hash conflict")
        before = self.body
        self.body = after_text
        self.published.append((before, after_text))
        return type("Published", (), {"before_text": before, "after_text": after_text, "index_result": {"docs": 1}, "line_delta": 0})()


class NonEditableWorkspace(FakeWorkspace):
    def read_editable_node(self, handle: str, *, allow_merged_member: bool = False):
        data = super().read_editable_node(
            handle,
            allow_merged_member=allow_merged_member,
        )
        data["editable"] = False
        data["editable_reason"] = "empty_text"
        return data


def admin() -> CurrentUser:
    return CurrentUser(id=1, username="admin", is_admin=True)


def normal() -> CurrentUser:
    return CurrentUser(id=2, username="normal", is_admin=False)


@pytest.mark.asyncio
async def test_get_document_state_initializes_missing_state() -> None:
    repo = FakeRepo()
    service = DoceditService(repo=repo, workspace=FakeWorkspace())

    state = await service.get_document_state("doc_sample", admin())

    assert state.version == 1
    assert state.can_edit is True


@pytest.mark.asyncio
async def test_get_node_returns_editable_body_for_admin() -> None:
    service = DoceditService(repo=FakeRepo(), workspace=FakeWorkspace())

    node = await service.get_node("doc_sample:0001", admin())

    assert node.text == "old"
    assert node.editable is True
    assert node.can_edit is True


@pytest.mark.asyncio
async def test_get_node_returns_can_edit_false_for_normal_user() -> None:
    service = DoceditService(repo=FakeRepo(), workspace=FakeWorkspace())

    node = await service.get_node("doc_sample:0001", normal())

    assert node.can_edit is False
    assert node.editable_reason == "not_admin"


@pytest.mark.asyncio
async def test_save_node_rejects_normal_user() -> None:
    service = DoceditService(repo=FakeRepo(), workspace=FakeWorkspace())

    with pytest.raises(DoceditForbidden):
        await service.save_node(
            "doc_sample:0001",
            NodeSaveRequest(
                base_version=1,
                base_hash=text_hash("old"),
                after_text="new",
                op_type="manual_edit",
            ),
            normal(),
        )


@pytest.mark.asyncio
async def test_save_node_rejects_base_version_conflict() -> None:
    repo = FakeRepo()
    repo.version = 2
    service = DoceditService(repo=repo, workspace=FakeWorkspace())

    with pytest.raises(DoceditConflict):
        await service.save_node(
            "doc_sample:0001",
            NodeSaveRequest(
                base_version=1,
                base_hash=text_hash("old"),
                after_text="new",
                op_type="manual_edit",
            ),
            admin(),
        )


@pytest.mark.asyncio
async def test_save_node_writes_history_and_bumps_version() -> None:
    repo = FakeRepo()
    workspace = FakeWorkspace()
    service = DoceditService(repo=repo, workspace=workspace)

    resp = await service.save_node(
        "doc_sample:0001",
        NodeSaveRequest(
            base_version=1,
            base_hash=text_hash("old"),
            after_text="new",
            op_type="manual_edit",
        ),
        admin(),
    )

    assert resp.version == 2
    assert resp.change_id == 1
    assert repo.changes[0].before_text == "old"
    assert workspace.body == "new"


@pytest.mark.asyncio
async def test_save_node_rejects_active_reparse() -> None:
    repo = FakeRepo()
    repo.active = True
    service = DoceditService(repo=repo, workspace=FakeWorkspace())

    with pytest.raises(DoceditConflict):
        await service.save_node(
            "doc_sample:0001",
            NodeSaveRequest(
                base_version=1,
                base_hash=text_hash("old"),
                after_text="new",
                op_type="manual_edit",
            ),
            admin(),
        )


@pytest.mark.asyncio
async def test_create_ai_diff_does_not_write_files_db_or_indexes() -> None:
    repo = FakeRepo()
    workspace = FakeWorkspace()

    class FakeAgent:
        def edit(self, *, text: str, instruction: str):
            return {"after_text": "new", "explanation": "changed"}

    service = DoceditService(repo=repo, workspace=workspace, agent=FakeAgent())

    diff = await service.create_ai_diff(
        "doc_sample:0001",
        base_version=1,
        base_hash=text_hash("old"),
        instruction="change",
        user=admin(),
    )

    assert diff.after_text == "new"
    assert workspace.body == "old"
    assert repo.changes == []


@pytest.mark.asyncio
async def test_create_ai_diff_rejects_non_editable_node_before_llm() -> None:
    class FailAgent:
        def edit(self, *, text: str, instruction: str):
            raise AssertionError("LLM should not be called")

    service = DoceditService(
        repo=FakeRepo(),
        workspace=NonEditableWorkspace(),
        agent=FailAgent(),
    )

    with pytest.raises(DoceditUnprocessableEntity):
        await service.create_ai_diff(
            "doc_sample:0001",
            base_version=1,
            base_hash=text_hash("old"),
            instruction="change",
            user=admin(),
        )


@pytest.mark.asyncio
async def test_rollback_rejects_normal_user_before_reading_workspace() -> None:
    class ExplodingWorkspace(FakeWorkspace):
        def read_editable_node(self, handle: str, *, allow_merged_member: bool = False):
            raise AssertionError("workspace should not be read for forbidden rollback")

    service = DoceditService(repo=FakeRepo(), workspace=ExplodingWorkspace())

    with pytest.raises(DoceditForbidden):
        await service.rollback_change(
            1,
            req=type("Req", (), {"base_version": 1, "base_hash": text_hash("old")})(),
            user=normal(),
        )

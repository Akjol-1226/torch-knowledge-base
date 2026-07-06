from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.docedit.models import DocumentChange, DocumentEditState, NodeEditState


class DoceditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_document_state(self, doc_id: str) -> DocumentEditState | None:
        return await self.session.get(DocumentEditState, doc_id)

    async def get_or_create_document_state(self, doc_id: str) -> DocumentEditState:
        state = await self.get_document_state(doc_id)
        if state is not None:
            return state
        now = datetime.now(UTC)
        state = DocumentEditState(doc_id=doc_id, current_version=1, created_at=now, updated_at=now)
        self.session.add(state)
        await self.session.flush()
        return state

    async def bump_document_version(self, doc_id: str) -> DocumentEditState:
        state = await self.get_or_create_document_state(doc_id)
        state.current_version += 1
        state.summary_status = "stale"
        state.updated_at = datetime.now(UTC)
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return state

    async def create_change(self, **kwargs) -> DocumentChange:
        change = DocumentChange(**kwargs)
        self.session.add(change)
        await self.session.flush()
        await self.session.refresh(change)
        return change

    async def list_changes(self, doc_id: str) -> list[DocumentChange]:
        result = await self.session.exec(
            select(DocumentChange)
            .where(DocumentChange.doc_id == doc_id)
            .order_by(col(DocumentChange.version_to).desc(), col(DocumentChange.id).desc())
        )
        return list(result.all())

    async def get_change(self, change_id: int) -> DocumentChange | None:
        return await self.session.get(DocumentChange, change_id)

    async def get_node_state(self, handle: str) -> NodeEditState | None:
        return await self.session.get(NodeEditState, handle)

    async def list_changed_node_handles(self, doc_id: str) -> list[str]:
        result = await self.session.exec(
            select(NodeEditState.node_handle).where(
                NodeEditState.doc_id == doc_id,
                NodeEditState.changed == True,  # noqa: E712
            )
        )
        return list(result.all())

    async def upsert_node_edit_state(
        self,
        *,
        doc_id: str,
        node_handle: str,
        original_hash: str,
        current_hash: str,
        last_change_id: int | None,
    ) -> object:
        state = await self.get_node_state(node_handle)
        if state is None:
            state = NodeEditState(
                doc_id=doc_id,
                node_handle=node_handle,
                original_hash=original_hash,
                current_hash=current_hash,
            )
        state.current_hash = current_hash
        state.changed = current_hash != state.original_hash
        state.last_change_id = last_change_id
        state.updated_at = datetime.now(UTC)
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return SimpleNamespace(
            node_handle=state.node_handle,
            doc_id=state.doc_id,
            original_hash=state.original_hash,
            current_hash=state.current_hash,
            changed=state.changed,
            last_change_id=state.last_change_id,
            updated_at=state.updated_at,
        )

    async def has_active_reparse(self, doc_id: str) -> bool:
        from app.modules.ingest.task_service import find_active_reparse

        return find_active_reparse(doc_id) is not None

    def has_active_reparse_sync(self, doc_id: str) -> bool:
        from app.modules.ingest.task_service import find_active_reparse

        return find_active_reparse(doc_id) is not None

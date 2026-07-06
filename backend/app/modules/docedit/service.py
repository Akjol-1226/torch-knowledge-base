from __future__ import annotations

import asyncio
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.modules.auth.dependencies import CurrentUser
from app.modules.docedit import workspace as workspace_mod
from app.modules.docedit.agent import DoceditAgentError, get_agent
from app.modules.docedit.diff import make_diff_html, text_hash
from app.modules.docedit.models import DocumentChange
from app.modules.docedit.repository import DoceditRepository
from app.modules.docedit.schemas import (
    AiDiffResponse,
    ChangeDetailResponse,
    DocumentStateResponse,
    EditableNodeResponse,
    HistoryItem,
    HistoryResponse,
    NodeSaveRequest,
    RollbackRequest,
    RollbackResponse,
    SaveNodeResponse,
)
from app.modules.docedit.service_exceptions import (
    DoceditBadRequest,  # noqa: F401 - re-exported for router/tests compatibility
    DoceditConflict,
    DoceditForbidden,
    DoceditNotFound,
    DoceditServiceUnavailable,  # noqa: F401 - re-exported for router/tests compatibility
    DoceditUnprocessableEntity,
    DoceditUpstreamError,
)

DOCEDIT_SAVE_LOCK = asyncio.Lock()


class DoceditService:
    def __init__(self, repo: Any, workspace: Any | None = None, agent: Any | None = None):
        self.repo = repo
        self.workspace = workspace or workspace_mod.WorkspaceAdapter()
        self.agent = agent

    async def get_document_state(self, doc_id: str, user: CurrentUser) -> DocumentStateResponse:
        state = await self.repo.get_or_create_document_state(doc_id)
        changed = await self.repo.list_changed_node_handles(doc_id)
        active = await self.repo.has_active_reparse(doc_id)
        return DocumentStateResponse(
            doc_id=doc_id,
            version=state.current_version,
            can_edit=user.is_admin and not active,
            active_reparse=active,
            changed_node_handles=changed,
        )

    async def get_node(
        self,
        handle: str,
        user: CurrentUser,
        *,
        allow_merged_member: bool = False,
    ) -> EditableNodeResponse:
        doc_id, _node_id = workspace_mod.parse_handle(handle)
        state = await self.repo.get_or_create_document_state(doc_id)
        data = self.workspace.read_editable_node(
            handle,
            allow_merged_member=allow_merged_member,
        )
        node_state = await self.repo.get_node_state(handle)
        active = await self.repo.has_active_reparse(doc_id)
        can_edit = bool(user.is_admin and data["editable"] and not active)
        reason = data.get("editable_reason")
        if not user.is_admin:
            reason = "not_admin"
        elif active:
            reason = "active_reparse"
        payload = dict(data)
        payload.pop("editable_reason", None)
        payload.pop("summary_status", None)
        return EditableNodeResponse(
            **payload,
            doc_version=state.current_version,
            can_edit=can_edit,
            editable_reason=reason,
            changed=bool(getattr(node_state, "changed", False)),
            summary_status=getattr(state, "summary_status", "clean"),
        )

    async def save_node(
        self,
        handle: str,
        req: NodeSaveRequest,
        user: CurrentUser,
        *,
        allow_merged_member: bool = False,
    ) -> SaveNodeResponse:
        return await self._save_node_internal(
            handle=handle,
            base_version=req.base_version,
            base_hash=req.base_hash,
            after_text=req.after_text,
            op_type=str(req.op_type.value if hasattr(req.op_type, "value") else req.op_type),
            user=user,
            ai_instruction=req.ai_instruction,
            ai_explanation=req.ai_explanation,
            allow_merged_member=allow_merged_member,
        )

    async def _save_node_internal(
        self,
        *,
        handle: str,
        base_version: int,
        base_hash: str,
        after_text: str,
        op_type: str,
        user: CurrentUser,
        ai_instruction: str | None = None,
        ai_explanation: str | None = None,
        source_change_id: int | None = None,
        source_change: DocumentChange | None = None,
        allow_merged_member: bool = False,
    ) -> SaveNodeResponse:
        if not user.is_admin:
            raise DoceditForbidden("只有管理员可以编辑文档")
        doc_id, node_id = workspace_mod.parse_handle(handle)
        async with DOCEDIT_SAVE_LOCK:
            if await self.repo.has_active_reparse(doc_id):
                raise DoceditConflict("文档正在重新解析，暂不能编辑")
            state = await self.repo.get_or_create_document_state(doc_id)
            if base_version != state.current_version:
                raise DoceditConflict("文档版本已变化，请刷新后重试")
            node = self.workspace.read_editable_node(
                handle,
                allow_merged_member=allow_merged_member,
            )
            if not node["editable"]:
                raise DoceditUnprocessableEntity(node.get("editable_reason") or "节点不可编辑")
            if base_hash != node["text_hash"]:
                raise DoceditConflict("节点正文已变化，请刷新后重试")
            if source_change is not None and node["text_hash"] != source_change.after_hash:
                raise DoceditConflict("该历史之后已有新改动，不能直接回退")
            before_hash = node["text_hash"]
            after_hash = text_hash(after_text)
            diff_html = make_diff_html(node["text"], after_text)
            change = await self.repo.create_change(
                doc_id=doc_id,
                version_from=state.current_version,
                version_to=state.current_version + 1,
                node_handle=handle,
                node_title=node["title"],
                node_path=node["path"],
                op_type=op_type,
                actor_user_id=user.id,
                actor_username=user.username,
                before_text=node["text"],
                after_text=after_text,
                before_hash=before_hash,
                after_hash=after_hash,
                diff_html=diff_html,
                ai_instruction=ai_instruction,
                ai_explanation=ai_explanation,
                source_change_id=source_change_id,
            )
            new_state = await self.repo.bump_document_version(doc_id)
            current_node_state = await self.repo.get_node_state(handle)
            original_hash = getattr(current_node_state, "original_hash", before_hash)
            node_state = await self.repo.upsert_node_edit_state(
                doc_id=doc_id,
                node_handle=handle,
                original_hash=original_hash,
                current_hash=after_hash,
                last_change_id=change.id,
            )
            published = None
            try:
                publish_fn = self.workspace.publish
                if self.workspace.__class__.__module__ == workspace_mod.__name__:
                    published = await run_in_threadpool(
                        publish_fn,
                        doc_id,
                        node_id,
                        base_hash,
                        after_text,
                        active_reparse_check=(
                            lambda: self.repo.has_active_reparse_sync(doc_id)
                            if hasattr(self.repo, "has_active_reparse_sync")
                            else False
                        ),
                        allow_merged_member=allow_merged_member,
                    )
                else:
                    published = publish_fn(
                        doc_id,
                        node_id,
                        base_hash,
                        after_text,
                        allow_merged_member=allow_merged_member,
                    )
            except Exception:
                if hasattr(self.repo, "session"):
                    await self.repo.session.rollback()
                raise
            try:
                if hasattr(self.repo, "session"):
                    await self.repo.session.commit()
            except Exception:
                if hasattr(self.repo, "session"):
                    await self.repo.session.rollback()
                snapshot = getattr(published, "snapshot", None)
                if snapshot is not None:
                    workspace_mod.restore_live_artifacts(snapshot)
                raise
            workspace_mod.cleanup_live_artifact_snapshot(getattr(published, "snapshot", None))
            return SaveNodeResponse(
                handle=handle,
                doc_id=doc_id,
                version=new_state.current_version,
                text=after_text,
                text_hash=after_hash,
                changed=bool(getattr(node_state, "changed", True)),
                change_id=change.id or 0,
                index_rebuilt=True,
            )

    async def create_ai_diff(
        self,
        handle: str,
        *,
        base_version: int,
        base_hash: str,
        instruction: str,
        user: CurrentUser,
        allow_merged_member: bool = False,
    ) -> AiDiffResponse:
        if not user.is_admin:
            raise DoceditForbidden("只有管理员可以使用 AI 编辑")
        doc_id, _ = workspace_mod.parse_handle(handle)
        if await self.repo.has_active_reparse(doc_id):
            raise DoceditConflict("文档正在重新解析，暂不能编辑")
        state = await self.repo.get_or_create_document_state(doc_id)
        if base_version != state.current_version:
            raise DoceditConflict("文档版本已变化，请刷新后重试")
        node = self.workspace.read_editable_node(
            handle,
            allow_merged_member=allow_merged_member,
        )
        if not node["editable"]:
            raise DoceditUnprocessableEntity(node.get("editable_reason") or "节点不可编辑")
        if base_hash != node["text_hash"]:
            raise DoceditConflict("节点正文已变化，请刷新后重试")
        try:
            agent = self.agent or get_agent()
            result = agent.edit(text=node["text"], instruction=instruction)
        except DoceditAgentError as exc:
            raise DoceditUpstreamError(str(exc)) from exc
        after_text = result["after_text"] if isinstance(result, dict) else result.after_text
        explanation = (
            result.get("explanation", "")
            if isinstance(result, dict)
            else result.explanation
        )
        return AiDiffResponse(
            handle=handle,
            before_text=node["text"],
            after_text=after_text,
            before_hash=node["text_hash"],
            after_hash=text_hash(after_text),
            diff_html=make_diff_html(node["text"], after_text),
            explanation=explanation,
        )

    async def list_history(self, doc_id: str, user: CurrentUser) -> HistoryResponse:
        state = await self.repo.get_or_create_document_state(doc_id)
        changes = await self.repo.list_changes(doc_id)
        return HistoryResponse(
            doc_id=doc_id,
            version=state.current_version,
            items=[
                HistoryItem(
                    change_id=c.id or 0,
                    version=c.version_to,
                    op_type=c.op_type,
                    node_handle=c.node_handle,
                    node_title=c.node_title,
                    node_path=c.node_path,
                    actor_username=c.actor_username,
                    created_at=c.created_at,
                    can_rollback=user.is_admin,
                )
                for c in changes
            ],
        )

    async def get_change(self, change_id: int, user: CurrentUser) -> ChangeDetailResponse:
        change = await self.repo.get_change(change_id)
        if change is None:
            raise DoceditNotFound("change not found")
        return ChangeDetailResponse(
            change_id=change.id or 0,
            doc_id=change.doc_id,
            version_from=change.version_from,
            version_to=change.version_to,
            node_handle=change.node_handle,
            before_text=change.before_text,
            after_text=change.after_text,
            diff_html=change.diff_html,
            ai_instruction=change.ai_instruction,
            ai_explanation=change.ai_explanation,
        )

    async def rollback_change(
        self,
        change_id: int,
        req: RollbackRequest,
        user: CurrentUser,
    ) -> RollbackResponse:
        if not user.is_admin:
            raise DoceditForbidden("只有管理员可以回滚修改")
        change = await self.repo.get_change(change_id)
        if change is None:
            raise DoceditNotFound("change not found")
        saved = await self._save_node_internal(
            handle=change.node_handle,
            base_version=req.base_version,
            base_hash=req.base_hash,
            after_text=change.before_text,
            op_type="rollback",
            user=user,
            source_change_id=change.id,
            source_change=change,
            allow_merged_member=True,
        )
        return RollbackResponse(
            doc_id=change.doc_id,
            rolled_back_change_id=change.id or 0,
            change_id=saved.change_id,
            version=saved.version,
            node_handle=change.node_handle,
            text_hash=saved.text_hash,
        )


def make_service(session) -> DoceditService:
    return DoceditService(DoceditRepository(session))

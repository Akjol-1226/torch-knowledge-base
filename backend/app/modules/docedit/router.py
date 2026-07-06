from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.docedit import models as _docedit_models  # noqa: F401
from app.modules.docedit.schemas import AiDiffRequest, NodeSaveRequest, RollbackRequest
from app.modules.docedit.service import (
    DoceditBadRequest,
    DoceditConflict,
    DoceditForbidden,
    DoceditNotFound,
    DoceditServiceUnavailable,
    DoceditUnprocessableEntity,
    DoceditUpstreamError,
    make_service,
)

router = APIRouter(prefix="/docedit", tags=["docedit"])


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DoceditForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, DoceditNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DoceditConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DoceditUnprocessableEntity):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, DoceditBadRequest):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DoceditUpstreamError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, DoceditServiceUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/documents/{doc_id}/state")
async def get_document_state(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).get_document_state(doc_id, user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/nodes/{handle:path}")
async def get_node(
    handle: str,
    physical: bool = False,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).get_node(
            handle,
            user,
            allow_merged_member=physical,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.patch("/nodes/{handle:path}")
async def save_node(
    handle: str,
    req: NodeSaveRequest,
    physical: bool = False,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).save_node(
            handle,
            req,
            user,
            allow_merged_member=physical,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/nodes/{handle:path}/ai-diff")
async def create_ai_diff(
    handle: str,
    req: AiDiffRequest,
    physical: bool = False,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).create_ai_diff(
            handle,
            base_version=req.base_version,
            base_hash=req.base_hash,
            instruction=req.instruction,
            user=user,
            allow_merged_member=physical,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/documents/{doc_id}/history")
async def list_history(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).list_history(doc_id, user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/changes/{change_id}")
async def get_change(
    change_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).get_change(change_id, user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/changes/{change_id}/rollback")
async def rollback_change(
    change_id: int,
    req: RollbackRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await make_service(session).rollback_change(change_id, req, user)
    except Exception as exc:
        raise _map_error(exc) from exc

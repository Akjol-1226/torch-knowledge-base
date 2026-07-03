from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.modules.docgen import service
from app.modules.docgen.schemas import (
    BlueprintListResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DraftRequest,
    DraftResponse,
    PatchPlanRequest,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/docgen", tags=["docgen"])


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.DocgenNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.DocgenBadRequest):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.DocgenUpstreamError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, service.DocgenServiceUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/blueprints", response_model=BlueprintListResponse)
def list_blueprints(kbs: str = "") -> BlueprintListResponse:
    parsed_kbs = [item.strip() for item in kbs.split(",") if item.strip()] or None
    try:
        return service.list_blueprints(kbs=parsed_kbs)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    try:
        return service.create_session(req.doc_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    try:
        return service.get_session(session_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions/{session_id}/plan", response_model=PlanResponse)
def create_plan(session_id: str, req: PlanRequest) -> PlanResponse:
    try:
        return service.create_plan(session_id, req.message)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.patch("/sessions/{session_id}/plan", response_model=PlanResponse)
def patch_plan(session_id: str, req: PatchPlanRequest) -> PlanResponse:
    try:
        return service.patch_plan(session_id, req.items)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions/{session_id}/draft", response_model=DraftResponse)
def create_draft(session_id: str, req: DraftRequest) -> DraftResponse:
    try:
        return service.create_draft(session_id, req.accepted_item_ids)
    except Exception as exc:
        raise _map_error(exc) from exc

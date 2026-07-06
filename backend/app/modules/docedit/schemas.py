from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SaveOpType(StrEnum):
    manual_edit = "manual_edit"
    ai_edit = "ai_edit"


class ChangeOpType(StrEnum):
    manual_edit = "manual_edit"
    ai_edit = "ai_edit"
    rollback = "rollback"


class DocumentStateResponse(BaseModel):
    doc_id: str
    version: int
    can_edit: bool
    active_reparse: bool = False
    changed_node_handles: list[str] = Field(default_factory=list)


class EditableNodeResponse(BaseModel):
    handle: str
    doc_id: str
    node_id: str
    title: str
    path: str
    text: str
    text_hash: str
    doc_version: int
    has_children: bool
    has_text: bool
    editable: bool
    editable_reason: str | None = None
    can_edit: bool
    changed: bool = False
    summary_status: str = "clean"


class NodeSaveRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: str
    after_text: str
    op_type: SaveOpType = SaveOpType.manual_edit
    ai_instruction: str | None = None
    ai_explanation: str | None = None

    @field_validator("after_text")
    @classmethod
    def normalize_after_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not normalized.strip():
            raise ValueError("after_text must not be empty")
        return normalized


SaveNodeRequest = NodeSaveRequest


class SaveNodeResponse(BaseModel):
    handle: str
    doc_id: str
    version: int
    text: str
    text_hash: str
    changed: bool
    change_id: int
    summary_status: str = "stale"
    index_rebuilt: bool = True


class AiDiffRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: str
    instruction: str


class AiDiffResponse(BaseModel):
    handle: str
    before_text: str
    after_text: str
    before_hash: str
    after_hash: str
    diff_html: str
    explanation: str


class HistoryItem(BaseModel):
    change_id: int
    version: int
    op_type: str
    node_handle: str
    node_title: str
    node_path: str
    actor_username: str
    created_at: datetime
    can_rollback: bool


class HistoryResponse(BaseModel):
    doc_id: str
    version: int
    items: list[HistoryItem]


class ChangeDetailResponse(BaseModel):
    change_id: int
    doc_id: str
    version_from: int
    version_to: int
    node_handle: str
    before_text: str
    after_text: str
    diff_html: str
    ai_instruction: str | None = None
    ai_explanation: str | None = None


class RollbackRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: str


class RollbackResponse(BaseModel):
    doc_id: str
    rolled_back_change_id: int
    change_id: int
    version: int
    node_handle: str
    text_hash: str

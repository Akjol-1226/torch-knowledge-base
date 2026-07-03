from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class DocgenStatus(StrEnum):
    waiting_for_requirement = "waiting_for_requirement"
    plan_ready = "plan_ready"
    draft_ready = "draft_ready"


class ChangeSourceType(StrEnum):
    user = "user"
    ai = "ai"


class BlueprintItem(BaseModel):
    doc_id: str
    doc_name: str
    doc_description: str = ""
    kb: str = "default"
    chapter_count: int = 0
    node_count: int = 0


class BlueprintListResponse(BaseModel):
    items: list[BlueprintItem]


class OutlineSummaryItem(BaseModel):
    node_id: str
    title: str
    summary: str = ""
    depth: int = 1


class CreateSessionRequest(BaseModel):
    doc_id: str = Field(min_length=1)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class BlueprintSummaryLLMResponse(BaseModel):
    summary: NonEmptyStr
    question: NonEmptyStr


class ChangePlanLLMItem(BaseModel):
    location: NonEmptyStr
    old_text: NonEmptyStr
    new_text: NonEmptyStr
    source_type: ChangeSourceType
    reason: NonEmptyStr
    source_handles: list[str]
    calculation_expression: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("source_handles")
    @classmethod
    def source_handles_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_handles must not be empty")
        return value


class ChangePlanLLMResponse(BaseModel):
    items: list[ChangePlanLLMItem]
    warnings: list[str] = Field(default_factory=list)


class ChangePlanItem(BaseModel):
    id: str
    node_id: str
    location: str
    old_text: str
    new_text: str
    source_type: ChangeSourceType
    source_label: str = ""
    reason: str
    source_handles: list[str]
    calculation_expression: str = ""
    calculation_result: str = ""
    calculation_note: str = ""
    selected: bool = True
    blocked: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)
    warning: str = ""

    @field_validator("source_handles")
    @classmethod
    def source_handles_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_handles must not be empty")
        return value

    @model_validator(mode="after")
    def fill_display_fields(self) -> ChangePlanItem:
        if not self.source_label:
            self.source_label = (
                "你指定" if self.source_type == ChangeSourceType.user else "AI推理"
            )
        if self.source_type == ChangeSourceType.ai and not self.warning:
            self.warning = "AI 推理值，必须人工核对。"
        return self


class PlanRequest(BaseModel):
    message: NonEmptyStr


class PlanResponse(BaseModel):
    session_id: str
    status: DocgenStatus
    plan: list[ChangePlanItem]
    warnings: list[str] = Field(default_factory=list)


class PatchPlanItem(BaseModel):
    id: str
    selected: bool


class PatchPlanRequest(BaseModel):
    items: list[PatchPlanItem]


class DraftRequest(BaseModel):
    accepted_item_ids: list[str] | None = None


class DraftResponse(BaseModel):
    session_id: str
    status: DocgenStatus
    title: str
    markdown: str
    copy_text: str
    highlighted_markdown: str
    highlight_count: int
    applied_item_ids: list[str]
    skipped_item_ids: list[str]
    unresolved_item_ids: list[str]
    warnings: list[str] = Field(default_factory=list)


class DocgenSessionRecord(BaseModel):
    session_id: str
    doc_id: str
    status: DocgenStatus
    blueprint: BlueprintItem
    summary: str
    outline: list[OutlineSummaryItem]
    question: str
    plan: list[ChangePlanItem] = Field(default_factory=list)
    draft: DraftResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateSessionResponse(BaseModel):
    session_id: str
    doc_id: str
    status: DocgenStatus
    blueprint: BlueprintItem
    summary: str
    outline: list[OutlineSummaryItem]
    question: str

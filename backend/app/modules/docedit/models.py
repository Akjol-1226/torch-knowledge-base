from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class DocumentEditState(SQLModel, table=True):
    doc_id: str = Field(primary_key=True)
    current_version: int = 1
    summary_status: str = "clean"
    active_summary_task_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentChange(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doc_id: str = Field(index=True)
    version_from: int
    version_to: int = Field(index=True)
    node_handle: str = Field(index=True)
    node_title: str
    node_path: str
    op_type: str
    actor_user_id: int | None = None
    actor_username: str
    before_text: str
    after_text: str
    before_hash: str
    after_hash: str
    diff_html: str
    ai_instruction: str | None = None
    ai_explanation: str | None = None
    source_change_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class NodeEditState(SQLModel, table=True):
    node_handle: str = Field(primary_key=True)
    doc_id: str = Field(index=True)
    original_hash: str
    current_hash: str
    changed: bool = False
    last_change_id: int | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    task_id: int
    document_id: int
    filename: str
    status: str
    page_count: int


class TaskItem(BaseModel):
    task_id: int
    document_id: int
    filename: str
    status: str
    progress: int
    error: str | None
    created_at: datetime
    updated_at: datetime

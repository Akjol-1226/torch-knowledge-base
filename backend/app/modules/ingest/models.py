from datetime import datetime

from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    sha256: str = Field(index=True, unique=True)
    page_count: int
    created_at: datetime


class Page(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    page_number: int
    raw_text: str | None = None
    notsure_count: int = 0
    extracted_at: datetime | None = None


class IngestTask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    status: str = Field(default="pending", index=True)
    progress: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime

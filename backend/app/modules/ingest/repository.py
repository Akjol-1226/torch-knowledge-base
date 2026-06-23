from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ingest.models import Document, IngestTask


class IngestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_document(
        self, filename: str, sha256: str, page_count: int
    ) -> Document:
        doc = Document(
            filename=filename,
            sha256=sha256,
            page_count=page_count,
            created_at=datetime.now(UTC),
        )
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        result = await self.session.exec(
            select(Document).where(Document.sha256 == sha256)
        )
        return result.first()

    async def create_ingest_task(self, document_id: int) -> IngestTask:
        now = datetime.now(UTC)
        task = IngestTask(
            document_id=document_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_task_status(
        self,
        task_id: int,
        *,
        status: str | None = None,
        progress: int | None = None,
        error: str | None = None,
    ) -> IngestTask:
        task = await self.session.get(IngestTask, task_id)
        if task is None:
            raise ValueError(f"IngestTask {task_id} not found")
        if status is not None:
            task.status = status
        if progress is not None:
            task.progress = progress
        if error is not None:
            task.error = error
        task.updated_at = datetime.now(UTC)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def list_tasks(self, status: str | None = None) -> list[IngestTask]:
        stmt = select(IngestTask)
        if status:
            stmt = stmt.where(IngestTask.status == status)
        result = await self.session.exec(stmt)
        return list(result.all())

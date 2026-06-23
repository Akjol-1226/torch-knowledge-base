import hashlib

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import get_logger
from app.modules.ingest.models import Document
from app.modules.ingest.repository import IngestRepository
from app.modules.ingest.schemas import TaskItem, UploadResponse

logger = get_logger("ingest.service")


class IngestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = IngestRepository(session)

    async def upload_document(self, filename: str, content: bytes) -> UploadResponse:
        sha256 = hashlib.sha256(content).hexdigest()
        existing = await self.repo.find_document_by_sha256(sha256)
        if existing:
            logger.info("upload_duplicate", filename=filename, sha256=sha256)
            doc = existing
        else:
            # v0 占位：PDF 页数解析未实现，固定 0
            doc = await self.repo.create_document(
                filename=filename, sha256=sha256, page_count=0
            )
        assert doc.id is not None

        task = await self.repo.create_ingest_task(document_id=doc.id)
        assert task.id is not None

        logger.info(
            "ingest_task_created",
            task_id=task.id,
            document_id=doc.id,
            filename=filename,
        )

        return UploadResponse(
            task_id=task.id,
            document_id=doc.id,
            filename=doc.filename,
            status=task.status,
            page_count=doc.page_count,
        )

    async def list_tasks(self) -> list[TaskItem]:
        tasks = await self.repo.list_tasks()
        items: list[TaskItem] = []
        for t in tasks:
            assert t.id is not None
            doc = await self.session.get(Document, t.document_id)
            items.append(
                TaskItem(
                    task_id=t.id,
                    document_id=t.document_id,
                    filename=doc.filename if doc else "",
                    status=t.status,
                    progress=t.progress,
                    error=t.error,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
            )
        return items

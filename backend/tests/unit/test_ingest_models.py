from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from app.modules.ingest.models import Document, IngestTask, Page


def _now() -> datetime:
    return datetime.now(UTC)


async def test_document_round_trip(db_session: SQLModelAsyncSession) -> None:
    doc = Document(
        filename="test.pdf",
        sha256="abc123",
        page_count=10,
        created_at=_now(),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    result = await db_session.exec(select(Document).where(Document.sha256 == "abc123"))
    found = result.first()
    assert found is not None
    assert found.filename == "test.pdf"


async def test_page_belongs_to_document(db_session: SQLModelAsyncSession) -> None:
    doc = Document(filename="x.pdf", sha256="hash1", page_count=2, created_at=_now())
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    assert doc.id is not None

    page = Page(document_id=doc.id, page_number=1, raw_text="hello")
    db_session.add(page)
    await db_session.commit()
    await db_session.refresh(page)

    assert page.document_id == doc.id


async def test_ingest_task_default_status(db_session: SQLModelAsyncSession) -> None:
    doc = Document(filename="y.pdf", sha256="hash2", page_count=1, created_at=_now())
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    assert doc.id is not None

    now = _now()
    task = IngestTask(
        document_id=doc.id,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.status == "pending"
    assert task.progress == 0

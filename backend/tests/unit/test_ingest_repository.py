from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from app.modules.ingest.repository import IngestRepository


async def test_create_document_returns_id(db_session: SQLModelAsyncSession) -> None:
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h1", page_count=5)
    assert doc.id is not None
    assert doc.filename == "a.pdf"


async def test_find_document_by_sha256(db_session: SQLModelAsyncSession) -> None:
    repo = IngestRepository(db_session)
    await repo.create_document(filename="a.pdf", sha256="hash_x", page_count=5)

    found = await repo.find_document_by_sha256("hash_x")
    assert found is not None
    assert found.filename == "a.pdf"

    missing = await repo.find_document_by_sha256("nope")
    assert missing is None


async def test_create_ingest_task(db_session: SQLModelAsyncSession) -> None:
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h2", page_count=5)
    assert doc.id is not None

    task = await repo.create_ingest_task(document_id=doc.id)
    assert task.status == "pending"
    assert task.document_id == doc.id


async def test_update_task_status(db_session: SQLModelAsyncSession) -> None:
    repo = IngestRepository(db_session)
    doc = await repo.create_document(filename="a.pdf", sha256="h3", page_count=5)
    assert doc.id is not None
    task = await repo.create_ingest_task(document_id=doc.id)
    assert task.id is not None

    updated = await repo.update_task_status(task.id, status="running", progress=50)
    assert updated.status == "running"
    assert updated.progress == 50

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession


@pytest.fixture
async def client(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """业务模块 integration test 的标准 fixture。

    用 FastAPI 官方推荐的 `app.dependency_overrides` 替换 get_db，
    业务开发者写新模块的 integration test 时**照抄本 fixture 即可**。
    """
    monkeypatch.setenv("LITELLM_BASE_URL", "http://test.local")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_DEFAULT_VLM_MODEL", "test-model")

    from app.core.config import get_settings
    from app.core.db import get_db
    from app.main import app

    get_settings.cache_clear()

    async def _override_get_db() -> AsyncIterator[SQLModelAsyncSession]:
        async with SQLModelAsyncSession(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def test_upload_creates_task(client: AsyncClient) -> None:
    # 遗留 DB 入库骨架（/ingest/upload）仍保留；真实入库走异步队列 /ingest/upload-pdf
    # （其端到端覆盖见 test_ingest_pdf.py：上传→任务 queued→后台跑→needs_review/done）
    fake_pdf = b"%PDF-1.4 fake content for test\n"
    files = {"file": ("test.pdf", fake_pdf, "application/pdf")}
    r = await client.post("/ingest/upload", files=files)

    assert r.status_code == 200
    payload = r.json()
    assert "task_id" in payload
    assert payload["status"] == "pending"
    assert payload["filename"] == "test.pdf"

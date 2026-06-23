from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

# 必须 import 所有 model 模块，让 SQLModel.metadata 收集 table 定义。
# 不导入 = 测试用 in-memory engine 跑 create_all 时这些表不会被创建。
from app.modules.ingest import models as _ingest_models  # noqa: F401


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[SQLModelAsyncSession]:
    async with SQLModelAsyncSession(db_engine, expire_on_commit=False) as session:
        yield session

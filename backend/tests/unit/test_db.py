from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession


class DummyItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str


async def test_can_create_and_query(db_session: SQLModelAsyncSession) -> None:
    item = DummyItem(name="alpha")
    db_session.add(item)
    await db_session.commit()

    result = await db_session.exec(select(DummyItem))
    items = result.all()
    assert len(items) == 1
    assert items[0].name == "alpha"

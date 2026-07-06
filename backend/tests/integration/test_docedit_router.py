from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.retrieval.store_provider import clear_tree_store_for_tests
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.docedit.router import router as docedit_router


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    clear_tree_store_for_tests()
    assert get_settings().data_dir == tmp_path
    yield tmp_path
    clear_tree_store_for_tests()
    get_settings.cache_clear()


def write_router_doc(data_dir: Path) -> None:
    md_path = data_dir / "md" / "default" / "sample.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# Sample\n## A\nold", encoding="utf-8")
    doc = {
        "id": "doc_sample",
        "type": "md",
        "kb": "default",
        "path": str(md_path),
        "doc_name": "Sample",
        "line_count": 3,
        "structure": [
            {
                "node_id": "0001",
                "title": "A",
                "line_num": 2,
                "text": "## A\nold",
                "summary": "",
                "nodes": [],
            }
        ],
    }
    (data_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (data_dir / "workspace" / "doc_sample.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / "catalog").mkdir(parents=True, exist_ok=True)
    (data_dir / "catalog" / "document_catalog.json").write_text("[]", encoding="utf-8")


async def admin_user() -> CurrentUser:
    return CurrentUser(id=1, username="admin", is_admin=True)


async def normal_user() -> CurrentUser:
    return CurrentUser(id=2, username="normal", is_admin=False)


@pytest.fixture
async def client(db_engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(docedit_router)
    write_router_doc(tmp_path)

    async def override_get_db() -> AsyncIterator[SQLModelAsyncSession]:
        async with SQLModelAsyncSession(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = admin_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def test_get_document_state_endpoint_admin(client: AsyncClient) -> None:
    r = await client.get("/docedit/documents/doc_sample/state")

    assert r.status_code == 200
    assert r.json()["version"] == 1
    assert r.json()["can_edit"] is True


async def test_get_node_endpoint_normal_user_readonly(
    db_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(docedit_router)
    write_router_doc(tmp_path)

    async def override_get_db() -> AsyncIterator[SQLModelAsyncSession]:
        async with SQLModelAsyncSession(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = normal_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/docedit/nodes/doc_sample%3A0001")

    assert r.status_code == 200
    assert r.json()["can_edit"] is False
    assert r.json()["editable_reason"] == "not_admin"


async def test_patch_node_endpoint_rejects_normal_user(
    db_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(docedit_router)
    write_router_doc(tmp_path)

    async def override_get_db() -> AsyncIterator[SQLModelAsyncSession]:
        async with SQLModelAsyncSession(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = normal_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.patch(
            "/docedit/nodes/doc_sample%3A0001",
            json={
                "base_version": 1,
                "base_hash": "sha256:bad",
                "after_text": "new",
                "op_type": "manual_edit",
            },
        )

    assert r.status_code == 403


async def test_patch_node_endpoint_saves_manual_edit(client: AsyncClient) -> None:
    node = (await client.get("/docedit/nodes/doc_sample%3A0001")).json()

    r = await client.patch(
        "/docedit/nodes/doc_sample%3A0001",
        json={
            "base_version": node["doc_version"],
            "base_hash": node["text_hash"],
            "after_text": "new",
            "op_type": "manual_edit",
        },
    )

    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["text"] == "new"


async def test_patch_node_endpoint_maps_conflict(client: AsyncClient) -> None:
    r = await client.patch(
        "/docedit/nodes/doc_sample%3A0001",
        json={
            "base_version": 99,
            "base_hash": "sha256:bad",
            "after_text": "new",
            "op_type": "manual_edit",
        },
    )

    assert r.status_code == 409


async def test_history_and_change_detail_endpoints_return_timeline(
    client: AsyncClient,
) -> None:
    node = (await client.get("/docedit/nodes/doc_sample%3A0001")).json()
    saved = await client.patch(
        "/docedit/nodes/doc_sample%3A0001",
        json={
            "base_version": node["doc_version"],
            "base_hash": node["text_hash"],
            "after_text": "new",
            "op_type": "manual_edit",
        },
    )

    history = await client.get("/docedit/documents/doc_sample/history")
    detail = await client.get(f"/docedit/changes/{saved.json()['change_id']}")

    assert history.status_code == 200
    assert history.json()["items"][0]["version"] == 2
    assert detail.status_code == 200
    assert detail.json()["after_text"] == "new"

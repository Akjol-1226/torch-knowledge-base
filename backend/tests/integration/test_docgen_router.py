from fastapi.testclient import TestClient

from app.main import app
from app.modules.docgen import router as docgen_router
from app.modules.docgen.schemas import (
    BlueprintItem,
    BlueprintListResponse,
    CreateSessionResponse,
    DocgenStatus,
    DraftResponse,
    PlanResponse,
)


def test_blueprints_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "list_blueprints",
        lambda kbs=None: BlueprintListResponse(
            items=[BlueprintItem(doc_id="doc_a", doc_name="工艺文件")]
        ),
    )

    res = TestClient(app).get("/docgen/blueprints")

    assert res.status_code == 200
    assert res.json()["items"][0]["doc_id"] == "doc_a"


def test_create_session_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_session",
        lambda doc_id: CreateSessionResponse(
            session_id="b1_1",
            doc_id=doc_id,
            status=DocgenStatus.waiting_for_requirement,
            blueprint=BlueprintItem(doc_id=doc_id, doc_name="工艺文件"),
            summary="蓝本摘要",
            outline=[],
            question="你想基于它做份什么新文档？",
        ),
    )

    res = TestClient(app).post("/docgen/sessions", json={"doc_id": "doc_a"})

    assert res.status_code == 200
    assert res.json()["summary"] == "蓝本摘要"


def test_plan_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: PlanResponse(
            session_id=session_id,
            status=DocgenStatus.plan_ready,
            plan=[],
            warnings=[],
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改成 1206"})

    assert res.status_code == 200
    assert res.json()["status"] == "plan_ready"


def test_draft_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_draft",
        lambda session_id, accepted_item_ids=None: DraftResponse(
            session_id=session_id,
            status=DocgenStatus.draft_ready,
            title="工艺文件（初稿）",
            markdown="正文",
            copy_text="正文",
            highlighted_markdown="正文",
            highlight_count=0,
            applied_item_ids=[],
            skipped_item_ids=[],
            unresolved_item_ids=[],
            warnings=["初稿仅供参考，必须经研发人员审核。"],
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/draft", json={})

    assert res.status_code == 200
    assert res.json()["status"] == "draft_ready"


def test_router_maps_not_found(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_session",
        lambda doc_id: (_ for _ in ()).throw(
            docgen_router.service.DocgenNotFound("missing")
        ),
    )

    res = TestClient(app).post("/docgen/sessions", json={"doc_id": "missing"})

    assert res.status_code == 404


def test_router_maps_bad_request(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "patch_plan",
        lambda session_id, items: (_ for _ in ()).throw(
            docgen_router.service.DocgenBadRequest("bad")
        ),
    )

    res = TestClient(app).patch(
        "/docgen/sessions/b1_1/plan",
        json={"items": [{"id": "missing", "selected": True}]},
    )

    assert res.status_code == 400


def test_router_maps_upstream_error(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: (_ for _ in ()).throw(
            docgen_router.service.DocgenUpstreamError("bad llm")
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改"})

    assert res.status_code == 502


def test_router_maps_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: (_ for _ in ()).throw(
            docgen_router.service.DocgenServiceUnavailable("index missing")
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改"})

    assert res.status_code == 503

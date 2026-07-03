import pytest

from app.modules.docgen import service
from app.modules.docgen.schemas import (
    BlueprintSummaryLLMResponse,
    ChangePlanLLMItem,
    ChangePlanLLMResponse,
    ChangeSourceType,
    DocgenStatus,
)


class FakeStore:
    def __init__(self):
        self.catalog = [
            {
                "doc_id": "doc_a",
                "doc_name": "工艺文件",
                "doc_description": "工艺",
                "kb": "default",
            }
        ]
        self.nodes = {
            "doc_a:0001": {
                "id": "doc_a:0001",
                "title": "1 范围",
                "summary": "范围摘要",
                "text": "0805 产品",
                "path": "工艺文件 > 1 范围",
                "cite": {"doc_id": "doc_a"},
                "has_children": False,
            }
        }

    def list_catalog(self, kbs=None):
        if kbs:
            return [x for x in self.catalog if x.get("kb") in set(kbs)]
        return self.catalog

    def get_outline(self, doc_id):
        if doc_id != "doc_a":
            return {"error": "missing"}
        return {
            "doc": "doc_a",
            "name": "工艺文件",
            "nodes": [
                {
                    "id": "doc_a:0001",
                    "title": "1 范围",
                    "summary": "范围摘要",
                    "lines": "1-1",
                    "has_children": False,
                }
            ],
        }

    def open_node(self, node_id):
        return {"node": node_id, "title": "1 范围", "children": []}

    def read_node(self, node_id):
        return self.nodes.get(node_id, {"error": "missing"})

    def search_nodes(self, query, top_k=8, kbs=None):
        return [
            {
                "id": "doc_a:0001",
                "title": "1 范围",
                "cite": {"doc_id": "doc_a"},
                "path": "工艺文件 > 1 范围",
            },
            {
                "id": "doc_b:0001",
                "title": "其它",
                "cite": {"doc_id": "doc_b"},
                "path": "其它 > 1",
            },
        ]


class FakeWrappedRootStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.catalog[0]["doc_name"] = "Blueprint Doc"

    def get_outline(self, doc_id):
        if doc_id != "doc_a":
            return {"error": "missing"}
        return {
            "doc": "doc_a",
            "name": "Blueprint Doc",
            "nodes": [
                {
                    "id": "doc_a:root",
                    "title": "Blueprint Doc",
                    "summary": "Wrapper root",
                    "has_children": True,
                }
            ],
        }

    def open_node(self, node_id):
        if node_id == "doc_a:root":
            return {
                "node": node_id,
                "title": "Blueprint Doc",
                "children": [
                    {
                        "id": "doc_a:0001",
                        "title": "Chapter 1",
                        "summary": "First chapter",
                    },
                    {
                        "id": "doc_a:0002",
                        "title": "Chapter 2",
                        "summary": "Second chapter",
                    },
                ],
            }
        return {"node": node_id, "children": []}


class FakeAgent:
    def summarize_blueprint(self, blueprint, outline):
        return BlueprintSummaryLLMResponse(summary="蓝本摘要", question="想生成什么？")

    def create_change_plan(self, *, session, message, node_text_by_id, current_plan):
        assert set(node_text_by_id) == {"doc_a:0001"}
        return ChangePlanLLMResponse(
            items=[
                ChangePlanLLMItem(
                    location="第 1 章",
                    old_text="0805",
                    new_text="1206",
                    source_type=ChangeSourceType.user,
                    reason="用户指定",
                    source_handles=["doc_a:0001"],
                    confidence=0.9,
                )
            ],
            warnings=[],
        )


class ConflictingCalculationAgent:
    def summarize_blueprint(self, blueprint, outline):
        return BlueprintSummaryLLMResponse(summary="蓝本摘要", question="想生成什么？")

    def create_change_plan(self, *, session, message, node_text_by_id, current_plan):
        return ChangePlanLLMResponse(
            items=[
                ChangePlanLLMItem(
                    location="第 1 章",
                    old_text="0805",
                    new_text="1206",
                    source_type=ChangeSourceType.ai,
                    reason="AI 推算",
                    source_handles=["doc_a:0001"],
                    calculation_expression="1 + 1",
                    confidence=0.4,
                )
            ],
            warnings=[],
        )


def _patch_deps(monkeypatch, tmp_path, store=None, agent=None):
    monkeypatch.setattr(service, "get_tree_store", lambda: store or FakeStore())
    monkeypatch.setattr(service, "get_agent", lambda: agent or FakeAgent())
    monkeypatch.setattr(
        service.storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )
    monkeypatch.setattr(
        service.document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )


def test_list_blueprints_counts_outline_nodes(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)

    result = service.list_blueprints()

    assert result.items[0].doc_id == "doc_a"
    assert result.items[0].chapter_count == 1
    assert result.items[0].node_count == 1


def test_list_blueprints_counts_direct_children_under_wrapper_root(
    monkeypatch,
    tmp_path,
):
    _patch_deps(monkeypatch, tmp_path, store=FakeWrappedRootStore())

    result = service.list_blueprints()

    assert result.items[0].chapter_count == 2
    assert result.items[0].node_count == 3


def test_create_session_saves_summary(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)

    result = service.create_session("doc_a")
    loaded = service.storage.load_session(result.session_id)

    assert result.status == DocgenStatus.waiting_for_requirement
    assert result.summary == "蓝本摘要"
    assert loaded is not None
    assert loaded.doc_id == "doc_a"


def test_create_plan_filters_retrieval_to_selected_blueprint(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    result = service.create_plan(session.session_id, "改成 1206")

    assert result.status == DocgenStatus.plan_ready
    assert result.plan[0].id == "chg_001"
    assert result.plan[0].node_id == "doc_a:0001"


def test_patch_plan_rejects_unknown_item(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    session = service.create_session("doc_a")
    service.create_plan(session.session_id, "改成 1206")

    with pytest.raises(service.DocgenBadRequest):
        service.patch_plan(session.session_id, [{"id": "missing", "selected": True}])


def test_create_draft_uses_parsed_markdown_without_ingesting(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        (
            '{"id":"doc_a","doc_name":"工艺文件","kb":"default",'
            f'"path":"{md.as_posix()}","structure":[]}}'
        ),
        encoding="utf-8",
    )
    session = service.create_session("doc_a")
    service.create_plan(session.session_id, "改成 1206")

    draft = service.create_draft(session.session_id)

    assert draft.markdown == "# 工艺\n\n1206 产品"
    assert draft.copy_text == "工艺\n\n1206 产品"
    assert not (tmp_path / "catalog").exists()


def test_create_plan_invalidates_existing_draft(monkeypatch, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        (
            '{"id":"doc_a","doc_name":"工艺文件","kb":"default",'
            f'"path":"{md.as_posix()}","structure":[]}}'
        ),
        encoding="utf-8",
    )
    session = service.create_session("doc_a")
    service.create_plan(session.session_id, "改成 1206")
    service.create_draft(session.session_id)

    service.create_plan(session.session_id, "继续调整")
    loaded = service.storage.load_session(session.session_id)

    assert loaded is not None
    assert loaded.status == DocgenStatus.plan_ready
    assert loaded.draft is None


def test_create_draft_never_applies_calculation_conflict_even_if_requested(
    monkeypatch,
    tmp_path,
):
    _patch_deps(monkeypatch, tmp_path, agent=ConflictingCalculationAgent())
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        (
            '{"id":"doc_a","doc_name":"工艺文件","kb":"default",'
            f'"path":"{md.as_posix()}","structure":[]}}'
        ),
        encoding="utf-8",
    )
    session = service.create_session("doc_a")
    plan = service.create_plan(session.session_id, "按公式推算")

    service.patch_plan(session.session_id, [{"id": "chg_001", "selected": True}])
    draft = service.create_draft(session.session_id, accepted_item_ids=["chg_001"])

    assert plan.plan[0].selected is False
    assert "AI 新值与计算器结果不一致" in plan.plan[0].warning
    assert draft.markdown == "# 工艺\n\n0805 产品"
    assert draft.unresolved_item_ids == ["chg_001"]

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.core.retrieval import document_reader
from app.core.retrieval.store_provider import get_tree_store
from app.modules.docgen import storage
from app.modules.docgen.agent import DocgenAgentError, get_agent
from app.modules.docgen.calculator import calculate
from app.modules.docgen.renderer import render_draft
from app.modules.docgen.schemas import (
    BlueprintItem,
    BlueprintListResponse,
    ChangePlanItem,
    ChangePlanLLMItem,
    ChangeSourceType,
    CreateSessionResponse,
    DocgenSessionRecord,
    DocgenStatus,
    DraftResponse,
    OutlineSummaryItem,
    PatchPlanItem,
    PlanResponse,
)


class DocgenNotFound(RuntimeError):
    pass


class DocgenBadRequest(RuntimeError):
    pass


class DocgenUpstreamError(RuntimeError):
    pass


class DocgenServiceUnavailable(RuntimeError):
    pass


def _new_session_id() -> str:
    return f"b1_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(6)}"


def _catalog_item(doc_id: str, kbs: list[str] | None = None) -> dict[str, Any] | None:
    for item in get_tree_store().list_catalog(kbs=kbs):
        if item.get("doc_id") == doc_id:
            return item
    return None


def _walk_outline(
    store: Any,
    node_id: str,
    depth: int,
    title: str,
    summary: str,
) -> list[OutlineSummaryItem]:
    out = [
        OutlineSummaryItem(
            node_id=node_id,
            title=title,
            summary=summary,
            depth=depth,
        )
    ]
    opened = store.open_node(node_id)
    for child in opened.get("children", []):
        if not isinstance(child, dict):
            continue
        out.extend(
            _walk_outline(
                store,
                str(child.get("id", "")),
                depth + 1,
                str(child.get("title", "")),
                str(child.get("summary", "")),
            )
        )
    return out


def _outline_for_doc(doc_id: str) -> list[OutlineSummaryItem]:
    store = get_tree_store()
    outline = store.get_outline(doc_id)
    if "error" in outline:
        return []
    out: list[OutlineSummaryItem] = []
    for node in outline.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        out.append(
            OutlineSummaryItem(
                node_id=node_id,
                title=str(node.get("title", "")),
                summary=str(node.get("summary", "")),
                depth=1,
            )
        )
        for child in store.open_node(node_id).get("children", []):
            if isinstance(child, dict):
                out.extend(
                    _walk_outline(
                        store,
                        str(child.get("id", "")),
                        2,
                        str(child.get("title", "")),
                        str(child.get("summary", "")),
                    )
                )
    return out


def _chapter_count_for_outline(
    doc_name: str,
    outline: list[OutlineSummaryItem],
) -> int:
    top_nodes = [item for item in outline if item.depth == 1]
    if len(top_nodes) == 1 and top_nodes[0].title.strip() == doc_name.strip():
        return len([item for item in outline if item.depth == 2])
    return len(top_nodes)


def _blueprint_from_catalog(item: dict[str, Any]) -> BlueprintItem:
    doc_id = str(item.get("doc_id", ""))
    doc_name = str(item.get("doc_name", ""))
    outline = _outline_for_doc(doc_id)
    return BlueprintItem(
        doc_id=doc_id,
        doc_name=doc_name,
        doc_description=str(item.get("doc_description", "")),
        kb=str(item.get("kb", "default")),
        chapter_count=_chapter_count_for_outline(doc_name, outline),
        node_count=len(outline),
    )


def list_blueprints(kbs: list[str] | None = None) -> BlueprintListResponse:
    items = [_blueprint_from_catalog(item) for item in get_tree_store().list_catalog(kbs=kbs)]
    return BlueprintListResponse(items=items)


def create_session(doc_id: str) -> CreateSessionResponse:
    item = _catalog_item(doc_id)
    if item is None:
        raise DocgenNotFound(f"未找到蓝本文档: {doc_id}")
    blueprint = _blueprint_from_catalog(item)
    outline = _outline_for_doc(doc_id)
    try:
        summary = get_agent().summarize_blueprint(blueprint, outline)
    except DocgenAgentError as exc:
        raise DocgenUpstreamError(str(exc)) from exc
    now = datetime.now(UTC)
    record = DocgenSessionRecord(
        session_id=_new_session_id(),
        doc_id=doc_id,
        status=DocgenStatus.waiting_for_requirement,
        blueprint=blueprint,
        summary=summary.summary,
        outline=outline,
        question=summary.question,
        created_at=now,
        updated_at=now,
    )
    storage.save_session(record)
    return CreateSessionResponse(
        session_id=record.session_id,
        doc_id=doc_id,
        status=record.status,
        blueprint=record.blueprint,
        summary=record.summary,
        outline=record.outline,
        question=record.question,
    )


def _load_session(session_id: str) -> DocgenSessionRecord:
    try:
        record = storage.load_session(session_id)
    except ValueError as exc:
        raise DocgenNotFound("会话不存在") from exc
    if record is None:
        raise DocgenNotFound("会话不存在")
    return record


def get_session(session_id: str) -> DocgenSessionRecord:
    return _load_session(session_id)


def _nodes_for_plan(record: DocgenSessionRecord, message: str) -> dict[str, str]:
    store = get_tree_store()
    hits = store.search_nodes(message, top_k=8, kbs=[record.blueprint.kb])
    if isinstance(hits, dict) and hits.get("error"):
        raise DocgenServiceUnavailable(str(hits["error"]))
    node_text_by_id: dict[str, str] = {}
    if not isinstance(hits, list):
        raise DocgenServiceUnavailable("检索结果格式异常")
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        node_id = str(hit.get("id", ""))
        cite = hit.get("cite") or {}
        if not isinstance(cite, dict):
            cite = {}
        if cite.get("doc_id") != record.doc_id and not node_id.startswith(f"{record.doc_id}:"):
            continue
        node = store.read_node(node_id)
        if "error" in node:
            continue
        node_text_by_id[node_id] = str(node.get("text", ""))
    if not node_text_by_id:
        raise DocgenBadRequest("未在蓝本文档中定位到可改动内容")
    return node_text_by_id


def _numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"[-+]?\d+(?:\.\d+)?", text or ""))


def _build_plan_item(
    idx: int,
    llm_item: ChangePlanLLMItem,
    allowed_node_ids: set[str],
    node_text_by_id: dict[str, str],
) -> ChangePlanItem:
    if any(handle not in allowed_node_ids for handle in llm_item.source_handles):
        raise DocgenUpstreamError("LLM 返回了蓝本文档范围外的 source_handles")
    if not any(llm_item.old_text in node_text_by_id[h] for h in llm_item.source_handles):
        raise DocgenUpstreamError("LLM 返回的 old_text is not present in source node text")

    calculation_result = ""
    calculation_note = ""
    selected = True
    blocked = False
    warning = ""
    if llm_item.calculation_expression:
        calc = calculate(llm_item.calculation_expression)
        if calc.ok:
            calculation_result = calc.result
            calculation_note = "计算器仅计算 AI 给出的算式，不代表业务公式已审核。"
            if (
                llm_item.source_type == ChangeSourceType.ai
                and calculation_result
                and calculation_result not in _numeric_tokens(llm_item.new_text)
            ):
                selected = False
                blocked = True
                warning = "AI 新值与计算器结果不一致，未自动应用；请人工核对。"
        else:
            selected = False
            blocked = True
            warning = f"计算失败：{calc.error}"

    return ChangePlanItem(
        id=f"chg_{idx:03d}",
        node_id=llm_item.source_handles[0],
        location=llm_item.location,
        old_text=llm_item.old_text,
        new_text=llm_item.new_text,
        source_type=llm_item.source_type,
        reason=llm_item.reason,
        source_handles=llm_item.source_handles,
        calculation_expression=llm_item.calculation_expression,
        calculation_result=calculation_result,
        calculation_note=calculation_note,
        selected=selected,
        blocked=blocked,
        confidence=llm_item.confidence,
        warning=warning,
    )


def create_plan(session_id: str, message: str) -> PlanResponse:
    record = _load_session(session_id)
    node_text_by_id = _nodes_for_plan(record, message)
    try:
        llm_plan = get_agent().create_change_plan(
            session=record,
            message=message,
            node_text_by_id=node_text_by_id,
            current_plan=record.plan,
        )
    except DocgenAgentError as exc:
        raise DocgenUpstreamError(str(exc)) from exc

    allowed_node_ids = set(node_text_by_id)
    plan = [
        _build_plan_item(idx, item, allowed_node_ids, node_text_by_id)
        for idx, item in enumerate(llm_plan.items, start=1)
    ]

    def mutate(rec: DocgenSessionRecord) -> DocgenSessionRecord:
        rec.status = DocgenStatus.plan_ready
        rec.plan = plan
        rec.draft = None
        rec.warnings = llm_plan.warnings
        rec.updated_at = datetime.now(UTC)
        return rec

    updated = storage.update_session(session_id, mutate)
    if updated is None:
        raise DocgenNotFound("会话不存在")
    return PlanResponse(
        session_id=session_id,
        status=updated.status,
        plan=updated.plan,
        warnings=updated.warnings,
    )


def patch_plan(session_id: str, items: Sequence[PatchPlanItem | dict[str, Any]]) -> PlanResponse:
    updates = [
        item if isinstance(item, PatchPlanItem) else PatchPlanItem.model_validate(item)
        for item in items
    ]

    def mutate(rec: DocgenSessionRecord) -> DocgenSessionRecord:
        known = {item.id for item in rec.plan}
        invalid = [item.id for item in updates if item.id not in known]
        if invalid:
            raise DocgenBadRequest(f"未知改动项: {', '.join(invalid)}")
        selected_by_id = {item.id: item.selected for item in updates}
        rec.plan = [
            item.model_copy(
                update={
                    "selected": False
                    if item.blocked
                    else selected_by_id.get(item.id, item.selected)
                }
            )
            for item in rec.plan
        ]
        rec.updated_at = datetime.now(UTC)
        return rec

    updated = storage.update_session(session_id, mutate)
    if updated is None:
        raise DocgenNotFound("会话不存在")
    return PlanResponse(
        session_id=session_id,
        status=updated.status,
        plan=updated.plan,
        warnings=updated.warnings,
    )


def create_draft(
    session_id: str,
    accepted_item_ids: list[str] | None = None,
) -> DraftResponse:
    record = _load_session(session_id)
    parsed = document_reader.read_parsed_document(record.doc_id)
    if parsed is None:
        raise DocgenNotFound("蓝本正文不存在")
    rendered = render_draft(
        markdown=parsed.markdown,
        title=record.blueprint.doc_name,
        items=record.plan,
        accepted_item_ids=accepted_item_ids,
    )
    draft = DraftResponse(
        session_id=session_id,
        status=DocgenStatus.draft_ready,
        title=rendered.title,
        markdown=rendered.markdown,
        copy_text=rendered.copy_text,
        highlighted_markdown=rendered.highlighted_markdown,
        highlight_count=rendered.highlight_count,
        applied_item_ids=rendered.applied_item_ids,
        skipped_item_ids=rendered.skipped_item_ids,
        unresolved_item_ids=rendered.unresolved_item_ids,
        warnings=rendered.warnings,
    )

    def mutate(rec: DocgenSessionRecord) -> DocgenSessionRecord:
        rec.status = DocgenStatus.draft_ready
        rec.draft = draft
        rec.updated_at = datetime.now(UTC)
        return rec

    updated = storage.update_session(session_id, mutate)
    if updated is None:
        raise DocgenNotFound("会话不存在")
    return draft

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from app.modules.docgen.schemas import ChangePlanItem, ChangeSourceType


@dataclass(frozen=True)
class DraftRenderResult:
    title: str
    markdown: str
    copy_text: str
    highlighted_markdown: str
    highlight_count: int
    applied_item_ids: list[str] = field(default_factory=list)
    skipped_item_ids: list[str] = field(default_factory=list)
    unresolved_item_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _copy_text(markdown: str) -> str:
    text = re.sub(r"</?mark[^>]*>", "", markdown)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    return text.strip()


def _highlight_class(item: ChangePlanItem) -> str:
    if item.source_type == ChangeSourceType.ai:
        return "docgen-change-ai"
    return "docgen-change-user"


def _replace_once(text: str, old: str, new: str) -> tuple[str, int]:
    count = text.count(old)
    if count != 1:
        return text, count
    return text.replace(old, new, 1), count


_HTML_CELL_RE = re.compile(
    r"^\s*<(?P<tag>td|th)\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</(?P=tag)>\s*$",
    re.IGNORECASE,
)


def _highlight_replacement(item: ChangePlanItem) -> str:
    mark_class = _highlight_class(item)
    old_cell = _HTML_CELL_RE.match(item.old_text)
    new_cell = _HTML_CELL_RE.match(item.new_text)
    if old_cell and new_cell:
        tag = new_cell.group("tag").lower()
        attrs = new_cell.group("attrs")
        body = html.escape(new_cell.group("body"))
        return f'<{tag}{attrs}><mark class="{mark_class}">{body}</mark></{tag}>'
    return f'<mark class="{mark_class}">{html.escape(item.new_text)}</mark>'


def render_draft(
    *,
    markdown: str,
    title: str,
    items: list[ChangePlanItem],
    accepted_item_ids: list[str] | None = None,
) -> DraftRenderResult:
    accepted = set(accepted_item_ids) if accepted_item_ids is not None else None
    draft = markdown
    highlighted = markdown
    applied: list[str] = []
    skipped: list[str] = []
    unresolved: list[str] = []
    warnings: list[str] = []

    for item in items:
        if item.blocked:
            unresolved.append(item.id)
            warnings.append(f"{item.id} 存在未解决风险，已跳过。")
            continue
        if not item.selected or (accepted is not None and item.id not in accepted):
            skipped.append(item.id)
            continue
        next_draft, count = _replace_once(draft, item.old_text, item.new_text)
        if count == 0:
            unresolved.append(item.id)
            warnings.append(f"{item.id} 未找到原文，已跳过。")
            continue
        if count > 1:
            unresolved.append(item.id)
            warnings.append(f"{item.id} 的原文出现多次，已跳过以避免误改。")
            continue

        mark = _highlight_replacement(item)
        highlighted, _ = _replace_once(highlighted, item.old_text, mark)
        draft = next_draft
        applied.append(item.id)

    for item in items:
        if item.id not in applied and item.id not in skipped and item.id not in unresolved:
            skipped.append(item.id)
    warnings.append("初稿仅供参考，必须经研发人员审核。")

    return DraftRenderResult(
        title=f"{title}（初稿）",
        markdown=draft,
        copy_text=_copy_text(draft),
        highlighted_markdown=highlighted,
        highlight_count=len(applied),
        applied_item_ids=applied,
        skipped_item_ids=skipped,
        unresolved_item_ids=unresolved,
        warnings=warnings,
    )

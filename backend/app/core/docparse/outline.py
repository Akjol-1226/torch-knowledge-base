from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import fitz

from .config import get_config
from .models import Heading, PageStructure, PageRegion, DocumentContext
from .qwen_client import call_vlm
from .prompts import OUTLINE_SYSTEM_PROMPT, build_outline_user_text
from .structure_enrich import (
    enrich_page_structure,
    parse_process_sections_from_toc_text,
    page_has_flowchart,
    recover_flowchart_page_heading,
)
from .utils import update_heading_stack

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_VLM_FAILURES = 3

# visual_prominence=="normal" 且 confidence 低于此阈值时视为正文列表项，丢弃
_PROMINENCE_CONF_THRESHOLD = 0.80


def _filter_vlm_list_items(headings: list[Heading]) -> list[Heading]:
    """丢弃 VLM 标记为 normal 且置信度不足的条目（正文编号要点误判为标题）。"""
    filtered: list[Heading] = []
    dropped: list[str] = []
    for h in headings:
        if h.visual_prominence == 'normal' and h.confidence < _PROMINENCE_CONF_THRESHOLD:
            dropped.append(h.text)
        else:
            filtered.append(h)
    if dropped:
        logger.debug('Phase1 VLM: 丢弃低视觉显著度标题 %d 条: %s', len(dropped), dropped)
    return filtered


class Phase1ParseError(RuntimeError):
    pass


def extract_phase1_tail(ps: PageStructure) -> str | None:
    parts: list[str] = []
    if ps.is_table_continuation:
        parts.append('（跨页表格延续中）')
    if ps.headings:
        parts.append(f'末尾标题：{ps.headings[-1].text}')
    if ps.appendix_headings:
        parts.append(f'末尾附件标题：{ps.appendix_headings[-1].text}')
    if ps.table_titles:
        titles_text = '、'.join(t.get('text', '') for t in ps.table_titles if t.get('text'))
        if titles_text:
            parts.append(f'本页表格：{titles_text}')
    if ps.notes:
        parts.append(f'备注：{ps.notes}')
    return ' | '.join(parts) if parts else None


def _parse_vlm_outline_response(raw: str, page_no: int) -> PageStructure:
    raw = raw.strip()
    # Extract the JSON object directly, tolerating code fences or surrounding text
    _json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if _json_match:
        raw = _json_match.group(0)
    data = json.loads(raw)

    def parse_heading(h: dict, htype: str) -> Heading:
        raw_prominence = h.get('visual_prominence', 'high')
        prominence = raw_prominence if raw_prominence in ('high', 'normal') else 'high'
        return Heading(
            text=h.get('text', ''),
            level=int(h.get('level', 2)),
            number=h.get('number') or None,
            type=htype,
            confidence=float(h.get('confidence', 0.5)),
            visual_prominence=prominence,
        )

    headings = _filter_vlm_list_items(
        [parse_heading(h, 'body_heading') for h in data.get('headings', [])]
    )
    appendix_headings = _filter_vlm_list_items(
        [parse_heading(h, 'appendix_heading') for h in data.get('appendix_headings', [])]
    )

    regions: list[PageRegion] = []
    for r in data.get('regions', []):
        bbox_raw = r.get('bbox')
        bbox = tuple(float(x) for x in bbox_raw) if bbox_raw else None
        regions.append(PageRegion(
            type=r.get('type', 'body'),
            bbox=bbox,
            notes=r.get('notes', ''),
        ))

    return PageStructure(
        page_no=int(data.get('page_no', page_no)),
        is_toc_page=bool(data.get('is_toc_page', False)),
        is_appendix_page=bool(data.get('is_appendix_page', False)),
        is_table_continuation=bool(data.get('is_table_continuation', False)),
        headings=headings,
        appendix_headings=appendix_headings,
        table_titles=data.get('table_titles', []),
        regions=regions,
        extraction_method='vlm',
        structure_confidence=0.70,
        notes=data.get('notes', ''),
    )


def extract_page_structure_vlm(
    image_path: Path,
    file_title: str,
    page_no: int,
    total_pages: int,
    previous_page_tail: str | None,
    heading_stack: list[Heading] | None = None,
) -> PageStructure:
    config = get_config()
    user_text = build_outline_user_text(
        file_title, page_no, total_pages, previous_page_tail,
        current_heading_stack=heading_stack,
    )
    raw = call_vlm(
        image_path=image_path,
        system_prompt=OUTLINE_SYSTEM_PROMPT,
        user_text=user_text,
        model=config.outline_model,
    )
    return _parse_vlm_outline_response(raw, page_no)


def run_phase1(
    pdf_path: str,
    page_images: list[Path],
    document_context: DocumentContext,
) -> tuple[DocumentContext, dict[int, str]]:
    consecutive_failures = 0
    phase1_tail: str | None = None
    page_raw_texts: dict[int, str] = {}
    heading_stack: list[Heading] = []  # 累积标题栈，逐页传给 VLM 提供跨页上下文

    with fitz.open(pdf_path) as fitz_doc:
        assert fitz_doc.page_count == len(page_images), (
            f"渲染页数({len(page_images)}) 与 PDF 页数({fitz_doc.page_count}) 不一致"
        )

        for page_no, image_path in enumerate(page_images, start=1):
            fitz_page = fitz_doc[page_no - 1]
            raw_text = fitz_page.get_text('text')
            page_raw_texts[page_no] = raw_text

            try:
                ps = extract_page_structure_vlm(
                    image_path=image_path,
                    file_title=document_context.file_title,
                    page_no=page_no,
                    total_pages=document_context.total_pages,
                    previous_page_tail=phase1_tail,
                    heading_stack=heading_stack,
                )
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                logger.warning('Phase 1 VLM parse failed page %d: %s', page_no, exc)
                if consecutive_failures >= MAX_CONSECUTIVE_VLM_FAILURES:
                    raise Phase1ParseError(
                        f'Phase 1 VLM failed {consecutive_failures} consecutive pages'
                    ) from exc
                ps = PageStructure(page_no=page_no, extraction_method='vlm')
                phase1_tail = None

            ps = enrich_page_structure(ps, raw_text)

            # VLM 兜底：流程图页标题漏报时从文本层恢复
            if not ps.is_toc_page and page_has_flowchart(ps, raw_text) and not ps.headings:
                recovered = recover_flowchart_page_heading(raw_text, heading_stack)
                if recovered:
                    ps.headings = [recovered]
                    logger.debug(
                        'Page %d: recovered flowchart heading "%s" from raw text',
                        page_no, recovered.text,
                    )

            document_context.page_structures[page_no] = ps

            # 用本页标题更新累积栈，供下一页 VLM 参考
            heading_stack = update_heading_stack(heading_stack, ps.headings + ps.appendix_headings)

            if ps.is_toc_page:
                document_context.toc_pages.append(page_no)
                if not document_context.process_sections:
                    document_context.process_sections = parse_process_sections_from_toc_text(raw_text)

            phase1_tail = extract_phase1_tail(ps)

    return document_context, page_raw_texts

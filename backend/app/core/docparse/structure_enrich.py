"""Post-process Phase 1 structures and heading helpers for Phase 2."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from .models import Heading, PageRegion, PageStructure, DocumentContext
from .utils import normalize_heading_text
from .heading_rules import (
    PROCESS_SECTION_RE,
    PROCESS_SECTION_LINE_RE,
    deepest_section_level,
    heading_level_for_title,
    is_appendix_table_title,
)

# 勿用单独的「流程图」：目录表里「…工艺生产流程图」等文件名会误判
FLOWCHART_PAGE_RE = re.compile(r'工艺流程图|生产工艺流程|工艺生产流程图', re.IGNORECASE)
# 流程图页顶部标题行特征：通常含产品型号 + 流程图关键词，长度合理（5–80字）
FLOWCHART_HEADING_LINE_RE = re.compile(
    r'^.{4,79}(?:工艺流程图|生产工艺流程图|工艺生产流程图|工序流程图)$'
)
TOC_PAGE_HINT_RE = re.compile(r'工艺文件目录|目\s*录', re.IGNORECASE)
FLOWCHART_CONTINUATION_RE = re.compile(r'续表|接上表|（续表）|\(续表\)')
# Phase 2 输出用的流程图小节名，不得进入标题栈
STACK_EXCLUDED_HEADING_RE = re.compile(
    r'^(流程图/架构图信息|节点列表|关系列表|流程链路总结|Mermaid\s*图|'
    r'图片内容描述|图表说明[:：]|工序参数详情|'
    r'工艺流程图|生产工艺流程图|工序流程图)$',
)

FLOWCHART_REQUIRED_SECTIONS = (
    '流程图/架构图信息',
    '节点列表',
    '关系列表',
    '流程链路总结',
    'Mermaid 图',
)


def is_stack_heading(text: str) -> bool:
    """Whether a ### line should update the document heading stack."""
    text = text.strip()
    if STACK_EXCLUDED_HEADING_RE.match(text):
        return False
    return True


def _is_toc_like_page(ps: PageStructure, raw_text: str = '') -> bool:
    if ps.is_toc_page:
        return True
    if TOC_PAGE_HINT_RE.search(raw_text[:400]):
        return True
    if ps.regions and all(r.type in ('toc', 'table') for r in ps.regions):
        return True
    return False


def _is_process_regulation_page(raw_text: str, ps: PageStructure) -> bool:
    """Gxx 工艺规程页不是整页工艺流程图，避免误触发流程图五小节模板。"""
    if PROCESS_SECTION_LINE_RE.search(raw_text[:500]):
        return True
    for h in ps.headings + ps.appendix_headings:
        if PROCESS_SECTION_LINE_RE.match(h.text.strip()):
            return True
    return False


def page_has_flowchart(ps: PageStructure, raw_text: str = '') -> bool:
    if _is_toc_like_page(ps, raw_text):
        return False
    if _is_process_regulation_page(raw_text, ps):
        return False
    blob = ' '.join(
        [h.text for h in ps.headings]
        + [h.text for h in ps.appendix_headings]
        + [ps.notes]
        + [r.notes for r in ps.regions if r.type != 'toc']
        + [raw_text[:800]]
    )
    if not FLOWCHART_PAGE_RE.search(blob):
        return False
    return True


def extract_process_section_headings(raw_text: str) -> list[Heading]:
    seen: set[str] = set()
    headings: list[Heading] = []
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if PROCESS_SECTION_LINE_RE.match(line):
            key = normalize_heading_text(line)
            if key not in seen:
                seen.add(key)
                headings.append(
                    Heading(text=line, level=3, number=None, type='body_heading', confidence=0.85)
                )
            continue
        for m in PROCESS_SECTION_RE.finditer(line):
            text = m.group(1).strip()
            key = normalize_heading_text(text)
            if key not in seen and len(text) >= 8:
                seen.add(key)
                headings.append(
                    Heading(text=text, level=3, number=None, type='body_heading', confidence=0.80)
                )
    return headings



def parse_process_sections_from_toc_text(raw_text: str) -> list[Heading]:
    """Parse G01-style entries from directory page text."""
    seen: set[str] = set()
    sections: list[Heading] = []
    for line in raw_text.split('\n'):
        for m in PROCESS_SECTION_RE.finditer(line):
            text = m.group(1).strip()
            key = normalize_heading_text(text)
            if key in seen:
                continue
            seen.add(key)
            sections.append(
                Heading(text=text, level=3, type='body_heading', confidence=0.75)
            )
    return sections


def merge_headings_unique(existing: list[Heading], new: list[Heading]) -> list[Heading]:
    keys = {normalize_heading_text(h.text) for h in existing}
    out = list(existing)
    for h in new:
        k = normalize_heading_text(h.text)
        if k not in keys:
            keys.add(k)
            out.append(h)
    return out


def _headings_from_table_titles_meta(
    table_titles: list[dict],
    section_level: int,
) -> list[Heading]:
    headings: list[Heading] = []
    seen: set[str] = set()
    last_appendix_level: int | None = None
    for item in table_titles:
        text = (item.get('text') or '').strip()
        if not text:
            continue
        key = normalize_heading_text(text)
        if key in seen:
            continue
        level = heading_level_for_title(
            text,
            section_level=section_level,
            appendix_level=last_appendix_level,
        )
        if level is None:
            continue
        seen.add(key)
        if is_appendix_table_title(text):
            last_appendix_level = level
            htype = 'appendix_heading'
        else:
            htype = 'table_title'
        headings.append(
            Heading(text=text, level=level, number=None, type=htype, confidence=0.8)
        )
    return headings


def extract_table_title_headings(raw_text: str, section_level: int = 2) -> list[Heading]:
    from .heading_rules import extract_table_title_headings_from_text

    return [
        Heading(text=t, level=lv, number=None, type=ht, confidence=0.85)
        for t, lv, ht in extract_table_title_headings_from_text(raw_text, section_level)
    ]


def align_process_headings_to_page(raw_text: str, headings: list[Heading]) -> list[Heading]:
    """Prefer full page line (e.g. 火炬电子 G01 …) when it matches a known G-section."""
    page_heads = extract_process_section_headings(raw_text)
    if not page_heads:
        return headings
    out = list(headings)
    keys = {normalize_heading_text(h.text) for h in out}
    for ph in page_heads:
        code_m = re.match(r'^(?:火炬电子\s+)?(G\d{1,2})', ph.text)
        if not code_m:
            continue
        code = code_m.group(1)
        replaced = False
        for i, h in enumerate(out):
            if code not in h.text or '工艺规程' not in h.text:
                continue
            if normalize_heading_text(ph.text) != normalize_heading_text(h.text):
                out[i] = Heading(
                    text=ph.text,
                    level=h.level,
                    number=h.number,
                    type=h.type,
                    confidence=max(h.confidence, ph.confidence),
                )
                keys.add(normalize_heading_text(ph.text))
            replaced = True
            break
        if not replaced and normalize_heading_text(ph.text) not in keys:
            out.append(ph)
            keys.add(normalize_heading_text(ph.text))
    return out


def enrich_page_structure(ps: PageStructure, raw_text: str = '') -> PageStructure:
    """Fix flowchart/table flags and inject headings from text layer."""
    if not _is_toc_like_page(ps, raw_text) and page_has_flowchart(ps, raw_text):
        if not FLOWCHART_CONTINUATION_RE.search(raw_text[:300]):
            ps.is_table_continuation = False
        if not any(r.type == 'figure' for r in ps.regions):
            ps.regions = list(ps.regions) + [
                PageRegion(type='figure', notes='工艺流程图/关系图区域')
            ]

    section_level = deepest_section_level(ps.headings + ps.appendix_headings)
    ps.headings = merge_headings_unique(
        ps.headings, _headings_from_table_titles_meta(ps.table_titles, section_level)
    )
    section_level = deepest_section_level(ps.headings + ps.appendix_headings)
    appendix_from_tables = [
        h for h in extract_table_title_headings(raw_text, section_level)
        if h.type == 'appendix_heading'
    ]
    plain_from_tables = [
        h for h in extract_table_title_headings(raw_text, section_level)
        if h.type == 'table_title'
    ]
    ps.appendix_headings = merge_headings_unique(ps.appendix_headings, appendix_from_tables)
    ps.headings = merge_headings_unique(ps.headings, plain_from_tables)

    return ps


def match_process_section_for_page(
    raw_text: str, process_sections: list[Heading]
) -> list[Heading]:
    if not process_sections:
        return []
    matched: list[Heading] = []
    norm_page = normalize_heading_text(raw_text)
    for sec in process_sections:
        key = normalize_heading_text(sec.text)
        code_m = re.match(r'^(?:火炬电子\s+)?(G\d{1,2})', sec.text.strip())
        if code_m and code_m.group(1) in norm_page:
            matched.append(sec)
        elif key and key in norm_page:
            matched.append(sec)
    return matched


def flowchart_output_complete(markdown: str) -> list[str]:
    missing = []
    for section in FLOWCHART_REQUIRED_SECTIONS:
        pattern = re.compile(r'^#{3,4}\s+' + re.escape(section) + r'\s*$', re.MULTILINE)
        if not pattern.search(markdown):
            missing.append(section)
    if '```mermaid' not in markdown:
        missing.append('mermaid_codeblock')
    return missing


def recover_flowchart_page_heading(
    raw_text: str,
    heading_stack: list[Heading],
) -> Heading | None:
    """VLM 漏报流程图页标题时的兜底恢复。

    Strategy 1：扫描页面前 30 行，取第一条匹配 FLOWCHART_HEADING_LINE_RE 的行作为标题。
    Strategy 2：若 raw_text 无文字（页面为纯图像），从 heading_stack 传播最近的流程图标题。
    level 均从 heading_stack 中同类流程图标题的 level 继承；若栈中无同类，默认 level=2。
    """
    # Strategy 1: 从文本层提取
    for line in raw_text.split('\n')[:30]:
        line = line.strip()
        if not FLOWCHART_HEADING_LINE_RE.match(line):
            continue
        level = 2
        for h in reversed(heading_stack):
            if FLOWCHART_PAGE_RE.search(h.text):
                level = h.level
                break
        return Heading(text=line, level=level, type='body_heading', confidence=0.75)

    # Strategy 2: 纯图像页——从 stack 传播最近的流程图标题
    for h in reversed(heading_stack):
        if FLOWCHART_PAGE_RE.search(h.text):
            return Heading(
                text=h.text,
                level=h.level,
                type=h.type,
                confidence=0.70,  # 稍低，表示来自传播而非直接识别
            )
    return None


_normalize_logger = logging.getLogger(__name__)


def normalize_global_headings(document_context: DocumentContext) -> None:
    """Phase 1 完成后的全局标题 level 归一化。

    对所有页 headings / appendix_headings 中出现的每个唯一标题文本，
    统计其跨页 level 分布，用多数投票（票数相同时取较小 level）确定「规范 level」，
    再把规范 level 写回所有页的对应标题对象。

    目的：消除 VLM 跨页随机性导致同一标题在不同页 level 不一致的问题。
    调用时机：run_phase1() 之后、precompute_page_contexts() 之前。
    """
    # Step 1: 统计每个 normalized text 的 level 出现次数
    level_counts: dict[str, Counter] = defaultdict(Counter)

    for ps in document_context.page_structures.values():
        for h in ps.headings + ps.appendix_headings:
            key = normalize_heading_text(h.text)
            if key:
                level_counts[key][h.level] += 1

    if not level_counts:
        return

    # Step 2: 多数投票——票数并列时选更小 level（更高层级，更保守）
    canonical: dict[str, int] = {}
    for key, counter in level_counts.items():
        max_votes = max(counter.values())
        candidates = [lv for lv, cnt in counter.items() if cnt == max_votes]
        canonical[key] = min(candidates)

    # Step 3: 记录变更，写回所有页
    changed: list[str] = []
    for ps in document_context.page_structures.values():
        for h in ps.headings + ps.appendix_headings:
            key = normalize_heading_text(h.text)
            if key in canonical and h.level != canonical[key]:
                changed.append(
                    f'p{ps.page_no} "{h.text[:30]}" {h.level}→{canonical[key]}'
                )
                h.level = canonical[key]

    if changed:
        _normalize_logger.info(
            'Global heading normalization: %d level adjustment(s): %s',
            len(changed), ', '.join(changed),
        )
    else:
        _normalize_logger.debug('Global heading normalization: all levels already consistent.')

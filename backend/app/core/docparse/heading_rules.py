"""Shared heading / table-title classification and redaction handling."""

from __future__ import annotations

import re
from typing import Any

MIN_SECTION_LEVEL = 2
MAX_HEADING_LEVEL = 6

APPENDIX_TABLE_TITLE_RE = re.compile(
    r'^附表\s*[\d一二三四五六七八九十]+(?:\s*[：:、\s]\s*)?.+$',
    re.UNICODE,
)

PLAIN_TABLE_TITLE_RE = re.compile(
    r'^表\s*\d[\d\-－–]*[\s\-—–].{1,60}$',
    re.UNICODE,
)

FIGURE_TITLE_RE = re.compile(
    r'^图\s*\d[\d\-－–]*',
    re.UNICODE,
)
FORMULA_TITLE_RE = re.compile(
    r'^公式\s*[（(]\s*\d+\s*[）)]',
    re.UNICODE,
)
ATTACHED_FIGURE_TITLE_RE = re.compile(
    r'^附图\s*\d[\d\-－–]*',
    re.UNICODE,
)

PROCESS_SECTION_LINE_RE = re.compile(
    r'^(?:火炬电子\s+)?G\d{1,2}(?:/G\d{1,2})?\s+.+?工艺规程',
)
PROCESS_SECTION_RE = re.compile(
    r'((?:火炬电子\s+)?G\d{1,2}(?:/G\d{1,2})?\s+[\u4e00-\u9fffA-Za-z0-9（()）/\-\s、]+?工艺规程(?:（[^）()]+）)?)',
)

APPENDIX_TABLE_LINE_RE = re.compile(
    r'^\s*(?:\*\*)?(附表\s*[\d一二三四五六七八九十]+(?:\s*[：:、\s]\s*)?.+?)(?:\*\*)?\s*$',
    re.UNICODE,
)
PLAIN_TABLE_LINE_RE = re.compile(
    r'^\s*(?:\*\*)?(表\s*\d[\d\-－–]*[^\n*]+?)(?:\*\*)?\s*$',
    re.UNICODE,
)

REDACTION_NOTSURE_BLOCK_RE = re.compile(
    r'<NOTSURE>\s*(?:'
    r'/+|\.{2,}|…+'
    r'|被涂抹|已遮挡|内容遮挡|故意遮挡|脱敏|已脱敏|测试过程图片'
    r'|不可见|无法辨认|看不清|模糊|涂抹|马赛克|黑块'
    r'|\[.*?\]'
    r'|\.{3}'
    r'|X{2,}(?:\s+X{2,})*'
    r'|x{2,}(?:\s+x{2,})*'
    r')\s*</NOTSURE>',
    re.IGNORECASE | re.UNICODE,
)
MALFORMED_NOTSURE_CLOSE_RE = re.compile(r'</NOTSUR>', re.IGNORECASE)

SIGNATURE_FIELD_CONTEXT_RE = re.compile(
    r'签名|拟制|审核|批准|填表|编制|会签|文控|质量|标准化|设计/日期|签名/日期|批准/日期',
    re.IGNORECASE,
)

REDACTION_KEYWORD_INNER_RE = re.compile(
    r'被涂抹|已遮挡|内容遮挡|故意遮挡|脱敏|已脱敏|测试过程图片'
    r'|不可见|无法辨认|看不清|模糊|涂抹|马赛克|黑块',
    re.IGNORECASE,
)


def is_redaction_notsure_inner(inner: str) -> bool:
    """Whether NOTSURE inner content is a redaction placeholder (should be blank, not tagged)."""
    inner = inner.strip()
    if not inner:
        return True
    if REDACTION_KEYWORD_INNER_RE.search(inner):
        return True
    if re.fullmatch(r'[\[\(【]?签名[\]\)】]?', inner):
        return True
    if re.fullmatch(r'[\*\.…/\\_#■\-—–]+', inner):
        return True
    if re.fullmatch(r'(?:X{2,}|x{2,})(?:\s+(?:X{2,}|x{2,}|\.\.+|…+))*', inner):
        return True
    compact = re.sub(r'\s', '', inner)
    if len(compact) >= 3:
        mask = sum(1 for c in compact if c in 'Xx*．.…/·')
        if mask / len(compact) >= 0.75:
            return True
    if re.search(r'X{3,}|x{3,}', inner):
        tail = re.sub(r'[Xx\s\.…·\-—–/\\*]+', '', inner)
        if len(tail) <= 2:
            return True
    return False

BOLD_APPENDIX_TABLE_LINE_RE = re.compile(
    r'^\*\*(附表\s*[\d一二三四五六七八九十]+(?:\s*[：:、\s]\s*)?.+?)\*\*\s*$',
    re.UNICODE,
)
BOLD_PLAIN_TABLE_LINE_RE = re.compile(
    r'^\*\*(表\s*\d[\d\-－–]*[^\n*]+?)\*\*\s*$',
    re.UNICODE,
)

HEADING_LINE_RE = re.compile(r'^(#{2,6})\s+(.+)$')


def _heading_text(h: Any) -> str:
    return (h.text if hasattr(h, 'text') else h.get('text', '')).strip()


def _heading_level(h: Any, default: int = MIN_SECTION_LEVEL) -> int:
    return int(h.level if hasattr(h, 'level') else h.get('level', default))


FLOWCHART_META_TITLE_RE = re.compile(
    r'^(工艺流程图|生产工艺流程图|工序流程图)$',
    re.IGNORECASE,
)


def is_flowchart_meta_title(text: str) -> bool:
    return bool(FLOWCHART_META_TITLE_RE.match(text.strip()))


def is_figure_or_formula_title(text: str) -> bool:
    text = text.strip()
    return bool(
        FIGURE_TITLE_RE.match(text)
        or FORMULA_TITLE_RE.match(text)
        or ATTACHED_FIGURE_TITLE_RE.match(text)
    )


def is_appendix_table_title(text: str) -> bool:
    return bool(APPENDIX_TABLE_TITLE_RE.match(text.strip()))


def is_plain_table_title(text: str) -> bool:
    text = text.strip()
    if is_appendix_table_title(text):
        return False
    return bool(PLAIN_TABLE_TITLE_RE.match(text))


def deepest_section_level(
    headings: list[Any],
    *,
    default: int = MIN_SECTION_LEVEL,
) -> int:
    """当前页/栈中最深层章节标题 level（不含附表、表题、图题）。"""
    levels: list[int] = []
    for h in headings:
        text = _heading_text(h)
        if is_appendix_table_title(text) or is_plain_table_title(text):
            continue
        if is_figure_or_formula_title(text):
            continue
        levels.append(_heading_level(h, default))
    return max(levels) if levels else default


def appendix_table_level(section_level: int) -> int:
    """附表：与当前最深层章节同级。"""
    return min(section_level, MAX_HEADING_LEVEL)


def plain_table_level(section_level: int, appendix_level: int | None = None) -> int:
    """普通表题：在附表之下 +1；无附表时在章节下 +1。"""
    base = appendix_level if appendix_level is not None else section_level
    return min(base + 1, MAX_HEADING_LEVEL)


def heading_level_for_title(
    text: str,
    *,
    section_level: int = MIN_SECTION_LEVEL,
    appendix_level: int | None = None,
) -> int | None:
    text = text.strip()
    if is_figure_or_formula_title(text):
        return None
    if is_appendix_table_title(text):
        return appendix_table_level(section_level)
    if is_plain_table_title(text):
        return plain_table_level(section_level, appendix_level)
    return None


def extract_table_title_headings_from_text(
    raw_text: str,
    section_level: int = MIN_SECTION_LEVEL,
) -> list[tuple[str, int, str]]:
    seen: set[str] = set()
    out: list[tuple[str, int, str]] = []
    last_appendix_level: int | None = None
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        for pat in (APPENDIX_TABLE_LINE_RE, PLAIN_TABLE_LINE_RE):
            m = pat.match(line)
            if not m:
                continue
            text = m.group(1).strip()
            key = text.replace(' ', '')
            if key in seen:
                continue
            if is_appendix_table_title(text):
                level = appendix_table_level(section_level)
                last_appendix_level = level
                htype = 'appendix_heading'
            elif is_plain_table_title(text):
                level = plain_table_level(section_level, last_appendix_level)
                htype = 'table_title'
            else:
                continue
            seen.add(key)
            out.append((text, level, htype))
    return out


def relevel_table_headings(headings: list[Any], section_level: int) -> list[Any]:
    """按父章节 level 重算附表/表题 level。"""
    from .models import Heading

    last_appendix_level: int | None = None
    result: list[Heading] = []
    for h in headings:
        text = _heading_text(h)
        if is_appendix_table_title(text):
            lv = appendix_table_level(section_level)
            last_appendix_level = lv
            result.append(
                Heading(
                    text=text,
                    level=lv,
                    number=getattr(h, 'number', None),
                    type='appendix_heading',
                    confidence=getattr(h, 'confidence', 0.8),
                )
            )
        elif is_plain_table_title(text):
            lv = plain_table_level(section_level, last_appendix_level)
            result.append(
                Heading(
                    text=text,
                    level=lv,
                    number=getattr(h, 'number', None),
                    type='table_title',
                    confidence=getattr(h, 'confidence', 0.8),
                )
            )
        else:
            result.append(h)
    return result

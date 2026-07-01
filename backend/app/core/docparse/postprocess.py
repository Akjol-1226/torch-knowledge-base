from __future__ import annotations

import re

from .heading_rules import (
    HEADING_LINE_RE,
    MALFORMED_NOTSURE_CLOSE_RE,
    REDACTION_NOTSURE_BLOCK_RE,
    SIGNATURE_FIELD_CONTEXT_RE,
    is_redaction_notsure_inner,
)
from .models import DocumentContext, Heading
from .structure_enrich import (
    FLOWCHART_REQUIRED_SECTIONS,
    STACK_EXCLUDED_HEADING_RE,
)
from .utils import normalize_heading_text, strip_notsure

_FLOWCHART_SECTION_ALT = '|'.join(re.escape(s) for s in FLOWCHART_REQUIRED_SECTIONS)
FLOWCHART_SECTION_HEADING_RE = re.compile(rf'^####\s+({_FLOWCHART_SECTION_ALT})\s*$')
NUMBERED_LIST_AS_HEADING_RE = re.compile(r'^(#{2,6})\s+(\d+[.．、])\s+(.+)$')
FLOWCHART_META_HEADING_RE = re.compile(
    r'^(#{2,6})\s+(工艺流程图|生产工艺流程图|工序流程图)\s*$',
    re.IGNORECASE,
)
PROCESS_REGULATION_BOLD_RE = re.compile(r'^\*\*(.+?工艺规程)\*\*\s*$')
FLOWCHART_LABEL_BOLD_RE = re.compile(r'^\*\*流程图\*\*\s*$')

# 仅降级图题、公式、附图（普通表题与附表保留为标题）
PAGE_MARKER_RE = re.compile(r'^<!-- page: \d+ -->\s*$')

FIGURE_FORMULA_HEADING_RE = re.compile(
    r'^(#{2,6})\s+'
    r'(图\s*\d[\d\-－–]*'
    r'|公式\s*[（(]\s*\d+\s*[）)]'
    r'|附图\s*\d[\d\-－–]*)'
    r'[\s\-—–]?',
    re.UNICODE,
)

APPENDIX_TOTAL_RE = re.compile(r'^(#{2,6})\s+(附件|附录)\s*$')
APPENDIX_ITEM_RE = re.compile(
    r'^(#{2,6})\s+(附件|附录)\s*[A-Za-z一二三四五六七八九十0-9]+'
    r'(?:[：:、\s].+)?$'
)
APPENDIX_SUB_RE = re.compile(
    r'^(#{2,6})\s+(附件|附录)\s*[\d一二三四五六七八九十A-Za-z]+'
    r'(?:\.\d+)+[：:、\s].+$'
)

_HTML_TABLE_OPEN_RE = re.compile(r'<table\b', re.IGNORECASE)
_HTML_TABLE_CLOSE_RE = re.compile(r'</table>', re.IGNORECASE)
_HTML_TBODY_OPEN_RE = re.compile(r'<tbody>', re.IGNORECASE)
_HTML_TBODY_CLOSE_RE = re.compile(r'</tbody>', re.IGNORECASE)
_SENTENCE_PUNCT_RE = re.compile(r'[；。！？]')
_TOC_ENTRY_HEADING_RE = re.compile(
    r'^(#{2,3})\s+(.+?)\s*[\.…·]{4,}\s*\d{1,3}\s*$'
)
_TOC_RUN_MIN = 3


def repair_unclosed_html_tables(text: str) -> str:
    """Close <table> tags missing </table> within each page block."""
    _page_split_re = re.compile(r'(<!-- page: \d+ -->)')
    parts = _page_split_re.split(text)
    result = []
    for part in parts:
        opens = len(_HTML_TABLE_OPEN_RE.findall(part))
        closes = len(_HTML_TABLE_CLOSE_RE.findall(part))
        unclosed = opens - closes
        if unclosed > 0:
            tbody_open = (
                len(_HTML_TBODY_OPEN_RE.findall(part))
                - len(_HTML_TBODY_CLOSE_RE.findall(part))
            )
            closing = ''
            for _ in range(unclosed):
                if tbody_open > 0:
                    closing += '\n</tbody>'
                    tbody_open -= 1
                closing += '\n</table>'
            part = part.rstrip('\n') + closing + '\n'
        result.append(part)
    return ''.join(result)


def demote_headings_in_html_table_cells(text: str) -> str:
    """Strip markdown heading markers from lines that appear inside HTML <td> blocks."""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    in_td = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
        if in_code:
            result.append(line)
            continue
        if PAGE_MARKER_RE.match(stripped):
            in_td = False
            result.append(line)
            continue
        td_open = bool(re.search(r'<td\b', stripped, re.IGNORECASE))
        td_close = bool(re.search(r'</td>', stripped, re.IGNORECASE))
        if td_open:
            in_td = True
        if in_td:
            m = HEADING_LINE_RE.match(line)
            if m:
                line = m.group(2)  # strip the '#...# ' prefix, keep content
            else:
                line = re.sub(r'(?<=>)(#{2,6})\s+', '', line)
        if td_close:
            in_td = False
        result.append(line)
    return '\n'.join(result)


def demote_toc_style_headings(text: str) -> str:
    """Convert runs of dotted TOC-entry headings (## chapter ....N) to list items.

    Runs of _TOC_RUN_MIN or more consecutive matching lines are converted; shorter
    runs are left untouched to avoid false positives on isolated dotted headings.
    """
    lines = text.split('\n')
    in_code = False

    # Pass 1: find indices of TOC-style heading lines (outside code blocks)
    toc_indices: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
        if not in_code and _TOC_ENTRY_HEADING_RE.match(line):
            toc_indices.append(i)

    if not toc_indices:
        return text

    # Pass 2: find runs of consecutive indices
    to_demote: set[int] = set()
    run_start = 0
    while run_start < len(toc_indices):
        run_end = run_start
        while (
            run_end + 1 < len(toc_indices)
            and toc_indices[run_end + 1] == toc_indices[run_end] + 1
        ):
            run_end += 1
        run_len = run_end - run_start + 1
        if run_len >= _TOC_RUN_MIN:
            for idx in range(run_start, run_end + 1):
                to_demote.add(toc_indices[idx])
        run_start = run_end + 1

    if not to_demote:
        return text

    # Pass 3: rewrite
    result: list[str] = []
    for i, line in enumerate(lines):
        if i in to_demote:
            m = _TOC_ENTRY_HEADING_RE.match(line)
            if m:
                result.append('- ' + m.group(2).strip())
                continue
        result.append(line)
    return '\n'.join(result)


def ensure_single_h1(text: str, file_title: str) -> str:
    lines = text.split('\n')
    demoted = []
    for ln in lines:
        if re.match(r'^# [^#]', ln):
            ln_title = ln[2:].strip()
            if ln_title == file_title:
                continue  # drop the canonical H1; we'll prepend a fresh one
            # Demote unrelated H1s to H2
            demoted.append('#' + ln)
        else:
            demoted.append(ln)
    body = '\n'.join(demoted).lstrip('\n')
    return f'# {file_title}\n\n{body}'


def _track_flowchart_block(line: str, in_flowchart: bool) -> bool:
    """进入/离开流程图 #### 补充小节范围（不含 ### 章节标题）。"""
    stripped = line.strip()
    if FLOWCHART_SECTION_HEADING_RE.match(stripped):
        return True
    if in_flowchart and NUMBERED_LIST_AS_HEADING_RE.match(line):
        return True
    m = HEADING_LINE_RE.match(line)
    if not m or not in_flowchart:
        return in_flowchart
    level = len(m.group(1))
    if level <= 3:
        return False
    if level == 4 and not FLOWCHART_SECTION_HEADING_RE.match(stripped):
        return False
    return True


def _skip_blank_lines(lines: list[str], start: int) -> int:
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i


def fix_flowchart_page_titles(text: str) -> str:
    """去掉误标的「### 工艺流程图」，将页眉「**…工艺规程**」「**流程图**」合并为 ### 标题。"""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            i += 1
            continue
        if in_code:
            result.append(line)
            i += 1
            continue

        if FLOWCHART_META_HEADING_RE.match(line.strip()):
            j = _skip_blank_lines(lines, i + 1)
            reg_title: str | None = None
            has_flow_label = False
            if j < len(lines):
                rm = PROCESS_REGULATION_BOLD_RE.match(lines[j].strip())
                if rm:
                    reg_title = rm.group(1).strip()
                    j = _skip_blank_lines(lines, j + 1)
                    if j < len(lines) and FLOWCHART_LABEL_BOLD_RE.match(lines[j].strip()):
                        has_flow_label = True
                        j += 1
            if reg_title:
                title = f'{reg_title} 流程图' if has_flow_label else reg_title
                result.append('### ' + title)
                i = j
                continue
            i += 1
            continue

        stripped = line.strip()
        _next = _skip_blank_lines(lines, i + 1)
        if (
            PROCESS_REGULATION_BOLD_RE.match(stripped)
            and _next < len(lines)
            and FLOWCHART_LABEL_BOLD_RE.match(lines[_next].strip())
        ):
            reg_title = PROCESS_REGULATION_BOLD_RE.match(stripped).group(1).strip()
            result.append(f'### {reg_title} 流程图')
            i = _next + 1
            continue

        result.append(line)
        i += 1
    return '\n'.join(result)


def demote_list_items_in_flowchart_sections(text: str) -> str:
    """流程图小节内误标为 ## 的 1. 2. 3. 列举改回正文。"""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    in_flowchart = False
    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        in_flowchart = _track_flowchart_block(line, in_flowchart)
        m = NUMBERED_LIST_AS_HEADING_RE.match(line)
        if in_flowchart and m:
            result.append(f'{m.group(2)} {m.group(3)}')
            continue
        result.append(line)
    return '\n'.join(result)


def _normalize_toc_content(text: str) -> str:
    lines = text.split('\n')
    result = []
    for line in lines:
        if re.match(r'^(#{1,6})\s+(目\s*录|Contents)\s*$', line, re.IGNORECASE):
            result.append('**目录**')
            continue
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            level = len(m.group(1))
            content = m.group(2)
            indent = '  ' * max(0, level - 2)
            result.append(f'{indent}- {content}')
        else:
            result.append(line)
    return '\n'.join(result)


def normalize_toc_blocks(text: str, toc_pages: list[int]) -> str:
    if not toc_pages:
        return text
    page_marker_re = re.compile(r'<!-- page: (\d+) -->')
    parts = page_marker_re.split(text)
    # parts = [before_first, page_no1, content1, page_no2, content2, ...]
    result = [parts[0]]
    i = 1
    while i < len(parts) - 1:
        page_no = int(parts[i])
        content = parts[i + 1]
        result.append(f'<!-- page: {page_no} -->')
        if page_no in toc_pages:
            result.append(_normalize_toc_content(content))
        else:
            result.append(content)
        i += 2
    return ''.join(result)


def normalize_appendix_headings(text: str) -> str:
    lines = text.split('\n')
    result = []
    in_code = False
    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if not in_code:
            if APPENDIX_TOTAL_RE.match(line):
                content = re.sub(r'^#{2,6}\s+', '', line)
                line = f'## {content}'
            elif APPENDIX_SUB_RE.match(line):
                content = re.sub(r'^#{2,6}\s+', '', line)
                line = f'#### {content}'
            elif APPENDIX_ITEM_RE.match(line):
                content = re.sub(r'^#{2,6}\s+', '', line)
                line = f'### {content}'
        result.append(line)
    return '\n'.join(result)


def insert_appendix_parent_nodes(text: str) -> str:
    lines = text.split('\n')
    result = []
    appendix_inserted: set[str] = set()
    appendix_re = re.compile(r'^## (附件|附录)\s*$')
    item_re = re.compile(r'^### (附件|附录)(.*)')
    for line in lines:
        m_parent = appendix_re.match(line)
        if m_parent:
            appendix_inserted.add(m_parent.group(1))
        m = item_re.match(line)
        if m and m.group(1) not in appendix_inserted:
            result.append(f'## {m.group(1)}')
            result.append('')
            appendix_inserted.add(m.group(1))
        result.append(line)
    return '\n'.join(result)


def normalize_redacted_notsure(text: str) -> str:
    """脱敏/遮挡区域不使用 NOTSURE，改为留空。"""
    text = REDACTION_NOTSURE_BLOCK_RE.sub('', text)
    text = MALFORMED_NOTSURE_CLOSE_RE.sub('</NOTSURE>', text)

    def _strip_redaction_inner(m: re.Match) -> str:
        inner = m.group(1)
        if is_redaction_notsure_inner(inner):
            return ''
        return m.group(0)

    text = re.sub(r'<NOTSURE>(.*?)</NOTSURE>', _strip_redaction_inner, text, flags=re.DOTALL)

    def _strip_signature_line_notsure(line: str) -> str:
        if '<NOTSURE>' not in line or not SIGNATURE_FIELD_CONTEXT_RE.search(line):
            return line
        line = re.sub(r'<NOTSURE>.*?</NOTSURE>', '', line, flags=re.DOTALL)
        return re.sub(r'<NOTSURE>.*$', '', line, flags=re.DOTALL)

    text = '\n'.join(_strip_signature_line_notsure(ln) for ln in text.split('\n'))
    text = '\n'.join(_strip_unclosed_notsure_line(ln) for ln in text.split('\n'))
    text = _strip_bare_redaction_placeholders(text)
    return text


def _strip_unclosed_notsure_line(line: str) -> str:
    """Handle dangling NOTSURE starts without deleting following document lines."""
    marker = '<NOTSURE>'
    close = '</NOTSURE>'
    while line.count(marker) > line.count(close):
        pos = line.rfind(marker)
        prefix = line[:pos]
        tail = line[pos + len(marker):]
        if is_redaction_notsure_inner(tail):
            line = prefix
        else:
            line = prefix + tail
    return line


def _strip_bare_redaction_placeholders(text: str) -> str:
    """Remove bare XXX / XXXX blocks that VLM wrote without NOTSURE wrappers."""
    text = re.sub(r'X{2,}(?=[\u4e00-\u9fff（(])', '', text)
    text = re.sub(r'(?:X{2,}|x{2,})(?:\s+(?:X{2,}|x{2,}|\.\.+|…+))+', '', text)
    text = re.sub(r'\(\s*X{2,}\s*\)', '()', text)
    return text


def demote_figure_formula_headings(text: str) -> str:
    """图题、公式编号、附图、流程图类型占位标题：从 # 标题降为加粗或删除。"""
    result = []
    in_code = False
    for line in text.splitlines(keepends=True):
        if line.startswith('```'):
            in_code = not in_code
        if not in_code:
            m = FIGURE_FORMULA_HEADING_RE.match(line.rstrip('\n'))
            if m:
                content = line[len(m.group(1)) + 1:].rstrip('\n').lstrip()
                line = f'**{content}**\n'
        result.append(line)
    return ''.join(result)


def _get_boundary_lines(page_text: str, n: int = 3) -> list[str]:
    lines = [ln for ln in page_text.splitlines() if ln.strip()]
    return lines[:n] + (lines[-n:] if len(lines) > n else [])


def _is_table_structure_line(clean: str) -> bool:
    if clean.startswith('|'):
        return True
    return bool(re.match(r'</?(?:table|thead|tbody|tr)\b', clean, re.IGNORECASE))


def deduplicate_headers_footers(pages: list[str]) -> list[str]:
    count: dict[str, int] = {}
    for page_text in pages:
        seen: set[str] = set()
        for line in _get_boundary_lines(page_text):
            clean = strip_notsure(line).strip()
            if _is_table_structure_line(clean):
                continue
            if clean and clean not in seen:
                count[clean] = count.get(clean, 0) + 1
                seen.add(clean)
    repeated = {k for k, v in count.items() if v >= 3}
    if not repeated:
        return pages
    result = []
    for page_text in pages:
        lines = page_text.splitlines(keepends=True)
        total = len(lines)
        boundary = set(range(min(3, total))) | set(range(max(0, total - 3), total))
        filtered = []
        for i, ln in enumerate(lines):
            clean = strip_notsure(ln).strip()
            if _is_table_structure_line(clean):
                filtered.append(ln)
                continue
            if i in boundary and clean in repeated and not HEADING_LINE_RE.match(clean):
                continue
            filtered.append(ln)
        result.append(''.join(filtered))
    return result


MERMAID_VALID_RE = re.compile(
    r'^(flowchart\s+(TD|LR|RL|BT|TB)'
    r'|graph\s+(TD|LR|RL|BT|TB)'
    r'|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|gantt|pie)',
    re.IGNORECASE,
)


def validate_and_annotate_mermaid(text: str) -> str:
    pattern = re.compile(r'(```mermaid\n)(.*?)(```)', re.DOTALL)

    def check(m: re.Match) -> str:
        first = m.group(2).lstrip('\n').split('\n')[0].strip()
        if not MERMAID_VALID_RE.match(first):
            return '> Mermaid 图疑似存在语法问题，已保留节点和关系列表作为主信息。\n\n' + m.group(0)
        return m.group(0)

    return pattern.sub(check, text)


PAGE_NUMBER_LINE_RE = re.compile(r'^第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?$')
HTML_DIV_LINE_RE = re.compile(r'^<div\b', re.IGNORECASE)


def _plain_text_from_html_div(line: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', line, flags=re.IGNORECASE)
    text = re.sub(r'</?div[^>]*>', '', text, flags=re.IGNORECASE)
    return text.strip()


def _is_page_footer_div(line: str) -> bool:
    plain = _plain_text_from_html_div(line)
    if not plain:
        return True
    lines = [ln.strip() for ln in plain.split('\n') if ln.strip()]
    return all(ln == '定型' or PAGE_NUMBER_LINE_RE.match(ln) for ln in lines)


def repair_unclosed_code_fences_at_page_markers(text: str) -> str:
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if PAGE_MARKER_RE.match(stripped) and in_code:
            result.append('```')
            in_code = False
        if stripped.startswith('```'):
            if not in_code:
                in_code = True
            elif stripped == '```':
                in_code = False
            else:
                result.append('```')
                in_code = True
        result.append(line)
    if in_code:
        result.append('```')
    return '\n'.join(result)


def strip_output_noise(text: str) -> str:
    """Remove page numbers, HTML footer blocks, and spurious cover headings."""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        stripped = line.strip()
        if PAGE_NUMBER_LINE_RE.match(stripped):
            continue
        if HTML_DIV_LINE_RE.match(stripped):
            if not _is_page_footer_div(line):
                for part in _plain_text_from_html_div(line).split('\n'):
                    part = part.strip()
                    if part and not PAGE_NUMBER_LINE_RE.match(part):
                        result.append(part)
            continue
        result.append(line)
    return '\n'.join(result)


def _split_pages(text: str) -> tuple[list[int], list[str]]:
    marker_re = re.compile(r'<!-- page: (\d+) -->\n?')
    parts = marker_re.split(text)
    page_nos: list[int] = []
    page_contents: list[str] = []
    i = 1
    while i < len(parts) - 1:
        page_nos.append(int(parts[i]))
        page_contents.append(parts[i + 1])
        i += 2
    # Preserve any content that appears before the first page marker
    prefix = parts[0]
    if prefix.strip() and page_contents:
        page_contents[0] = prefix + '\n' + page_contents[0]
    return page_nos, page_contents


_MD_HEADING_RE = re.compile(r'^(#{2,6})\s+(.+)$')

_HEADING_LINE_FOR_CORRECTION_RE = re.compile(r'^(#{1,6})\s+(.+)$')
_MIN_HEADING_CHARS_FOR_FUZZY = 4
_TH_CONTENT_RE = re.compile(r'<th(?:[^>]*)>(.*?)</th>', re.DOTALL | re.IGNORECASE)
_HTML_STRIP_TAGS_RE = re.compile(r'<[^>]+>')
_PLAIN_TITLE_LINE_MAX_CHARS = 150
_PLAIN_TITLE_MIN_HEADING_RATIO = 0.5


def _extract_th_texts(block: str) -> list[str]:
    return [
        _HTML_STRIP_TAGS_RE.sub('', m.group(1)).strip()
        for m in _TH_CONTENT_RE.finditer(block)
    ]


def _is_plain_title_candidate(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s[0] in ('#', '|', '<', '-', '*', '>', '`'):
        return False
    if re.match(r'^\d+[.、．]\s', s):
        return False
    if len(s) > _PLAIN_TITLE_LINE_MAX_CHARS:
        return False
    if _SENTENCE_PUNCT_RE.search(s):
        return False
    return True


def _is_heading_present(h_key: str, existing_keys: set[str]) -> bool:
    if h_key in existing_keys:
        return True
    return any(
        (h_key in k or k in h_key)
        for k in existing_keys
        if len(k) >= _MIN_HEADING_CHARS_FOR_FUZZY
    )


def _inject_from_html_th(content: str, missing: list) -> str:
    lines = content.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.match(r'\s*<table\b', line, re.IGNORECASE):
            out.append(line)
            i += 1
            continue
        table_buf = [line]
        depth = (len(re.findall(r'<table\b', line, re.IGNORECASE))
                 - len(re.findall(r'</table>', line, re.IGNORECASE)))
        i += 1
        while i < len(lines) and depth > 0:
            ln = lines[i]
            depth += (len(re.findall(r'<table\b', ln, re.IGNORECASE))
                      - len(re.findall(r'</table>', ln, re.IGNORECASE)))
            table_buf.append(ln)
            i += 1
        table_block = '\n'.join(table_buf)
        th_texts = _extract_th_texts(table_block)
        for h in list(missing):
            h_key = normalize_heading_text(h.text).lower()
            for th in th_texts:
                th_key = normalize_heading_text(th).lower()
                if len(th_key) < _MIN_HEADING_CHARS_FOR_FUZZY:
                    continue
                if h_key == th_key or h_key in th_key or th_key in h_key:
                    out.append('#' * h.level + ' ' + h.text)
                    missing.remove(h)
                    break
        out.extend(table_buf)
    return '\n'.join(out)


def _inject_from_plain_line(content: str, missing: list) -> str:
    if not missing:
        return content
    lines = content.split('\n')
    out: list[str] = []
    in_code = False
    in_html = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
        if in_code:
            out.append(line)
            continue
        if re.search(r'<table\b', stripped, re.IGNORECASE):
            in_html = True
        if '</table>' in stripped.lower():
            in_html = False
        if in_html:
            out.append(line)
            continue
        if _is_plain_title_candidate(line):
            line_key = normalize_heading_text(stripped).lower()
            matched = None
            for h in missing:
                h_key = normalize_heading_text(h.text).lower()
                if len(h_key) < _MIN_HEADING_CHARS_FOR_FUZZY:
                    continue
                ratio = len(h_key) / max(len(line_key), 1)
                if h_key == line_key or (
                    h_key in line_key and ratio > _PLAIN_TITLE_MIN_HEADING_RATIO
                ):
                    matched = h
                    break
            if matched:
                out.append('#' * matched.level + ' ' + matched.text)
                missing.remove(matched)
                continue
        out.append(line)
    return '\n'.join(out)


def inject_missing_headings_from_outline(
    text: str,
    document_context: DocumentContext,
) -> str:
    """Insert headings that Phase 2 buried in HTML <th> cells or plain text lines.

    For each page block, identifies P1.5 headings absent from Markdown output and
    finds them in HTML table headers or as standalone plain text lines.  Only
    injects when the text is actually found in the page content to avoid false
    positives.  flowchart_section headings and TOC pages are excluded.
    """
    if not document_context.page_structures:
        return text

    _page_marker_re = re.compile(r'(<!-- page: (\d+) -->)')
    parts = _page_marker_re.split(text)
    result: list[str] = [parts[0]]

    i = 1
    while i < len(parts) - 2:
        full_marker = parts[i]
        pno = int(parts[i + 1])
        content = parts[i + 2]

        ps = document_context.page_structures.get(pno)
        if ps is None or ps.is_toc_page:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        target_headings = [
            h for h in ps.headings
            if h.type != 'flowchart_section'
            and len(normalize_heading_text(h.text)) >= _MIN_HEADING_CHARS_FOR_FUZZY
        ]
        if not target_headings:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        existing_keys: set[str] = set()
        in_code = False
        for line in content.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
            if in_code:
                continue
            m = _HEADING_LINE_FOR_CORRECTION_RE.match(line)
            if m:
                existing_keys.add(normalize_heading_text(m.group(2).strip()).lower())

        missing = [
            h for h in target_headings
            if not _is_heading_present(normalize_heading_text(h.text).lower(), existing_keys)
        ]
        if not missing:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        content = _inject_from_html_th(content, missing)
        content = _inject_from_plain_line(content, missing)

        result.append(full_marker)
        result.append(content)
        i += 3

    if i < len(parts):
        result.extend(parts[i:])
    return ''.join(result)


def _heading_match_key(text: str) -> str:
    """归一化标题文本用于跨 Phase 匹配：在 normalize_heading_text 基础上去除所有内部空白。

    VLM 常在字母与汉字间插入空格（如 'CTK41B 型'），而 Phase 1 记的是 'CTK41B型'；
    去空白后两者一致，避免误判为「Phase 1 不存在的标题」而被降级。
    """
    return re.sub(r'\s+', '', normalize_heading_text(text)).lower()


_REDACTION_RUN_RE = re.compile(r'[xＸ]{2,}')


def _find_p15_level(heading_text: str, p15_headings: list) -> int | None:
    """Find the Phase 1.5 level for a heading text using exact then fuzzy match.

    脱敏占位(XXX)容错：Phase 1 文本常含 '(XXX瓷粉)'，而最终已去占位成 '(瓷粉)'，
    精确匹配时对两侧都剥去 X 连写后再比较。
    """
    norm_p2 = _heading_match_key(heading_text)
    if not norm_p2:
        return None
    red_p2 = _REDACTION_RUN_RE.sub('', norm_p2)
    # 精确匹配不受长度阈值限制：短标题（如「目的」「范围」）只要与 P1 完全相等即命中
    for h in p15_headings:
        norm_p1 = _heading_match_key(h.text)
        if norm_p2 == norm_p1 or red_p2 == _REDACTION_RUN_RE.sub('', norm_p1):
            return h.level
    # 模糊（子串）匹配易误命中，仅对足够长的文本启用
    if len(norm_p2) < _MIN_HEADING_CHARS_FOR_FUZZY:
        return None
    for h in p15_headings:
        norm_p1 = _heading_match_key(h.text)
        if len(norm_p1) < _MIN_HEADING_CHARS_FOR_FUZZY:
            continue
        if norm_p2 in norm_p1 or norm_p1 in norm_p2:
            return h.level
    return None


def correct_heading_levels_from_outline(
    text: str,
    document_context: DocumentContext,
) -> str:
    """Correct Phase 2 heading levels to match Phase 1.5 outline data.

    Splits by <!-- page: N --> markers and for each page uses **that page's**
    Phase 1.5 heading level as the authority (no global-min flattening — the
    same heading text may legitimately sit at different depths in different
    sections, e.g. 附表1).
    Exclusions: H1, code blocks, STACK_EXCLUDED_HEADING_RE matches.
    """
    if not document_context.page_structures:
        return text

    _page_marker_re = re.compile(r'(<!-- page: (\d+) -->)')
    parts = _page_marker_re.split(text)
    result: list[str] = [parts[0]]

    i = 1
    while i < len(parts) - 2:
        full_marker = parts[i]
        pno = int(parts[i + 1])
        content = parts[i + 2]

        ps = document_context.page_structures.get(pno)
        if ps is None:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        p15_headings = ps.headings + ps.appendix_headings
        if not p15_headings:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        lines = content.split('\n')
        corrected: list[str] = []
        in_code = False
        for line in lines:
            if line.strip().startswith('```'):
                in_code = not in_code
                corrected.append(line)
                continue
            if in_code:
                corrected.append(line)
                continue
            m = _HEADING_LINE_FOR_CORRECTION_RE.match(line)
            if not m:
                corrected.append(line)
                continue
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            if level == 1 or STACK_EXCLUDED_HEADING_RE.match(heading_text):
                corrected.append(line)
                continue
            p15_level = _find_p15_level(heading_text, p15_headings)
            if p15_level is not None:
                if p15_level != level:
                    corrected.append('#' * p15_level + ' ' + heading_text)
                    continue
            corrected.append(line)

        result.append(full_marker)
        result.append('\n'.join(corrected))
        i += 3

    if i < len(parts):
        result.extend(parts[i:])
    return ''.join(result)


def filter_unmatched_p2_headings(
    text: str,
    document_context: DocumentContext,
) -> str:
    """Demote Phase 2 headings with no Phase 1.5 match to bold text.

    Keeps H1 (document title), TOC pages, pages without P1.5 structure, and
    pages whose P1.5 headings include a flowchart_section (Phase 2 generates
    sub-section headings for flowcharts that P1.5 doesn't list individually).
    Code block contents are never touched.
    """
    if not document_context.page_structures:
        return text

    _page_marker_re = re.compile(r'(<!-- page: (\d+) -->)')
    parts = _page_marker_re.split(text)
    result: list[str] = [parts[0]]

    i = 1
    while i < len(parts) - 2:
        full_marker = parts[i]
        pno = int(parts[i + 1])
        content = parts[i + 2]

        ps = document_context.page_structures.get(pno)
        if ps is None or ps.is_toc_page:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        has_flowchart = any(h.type == 'flowchart_section' for h in ps.headings)
        if has_flowchart:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        p15_headings = ps.headings + ps.appendix_headings
        if not p15_headings:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        lines = content.split('\n')
        corrected: list[str] = []
        in_code = False
        for line in lines:
            if line.strip().startswith('```'):
                in_code = not in_code
                corrected.append(line)
                continue
            if in_code:
                corrected.append(line)
                continue
            m = HEADING_LINE_RE.match(line)
            if not m:
                corrected.append(line)
                continue
            heading_text = m.group(2).strip()
            if _find_p15_level(heading_text, p15_headings) is not None:
                corrected.append(line)
            else:
                corrected.append(f'**{heading_text}**')

        result.append(full_marker)
        result.append('\n'.join(corrected))
        i += 3

    if i < len(parts):
        result.extend(parts[i:])
    return ''.join(result)


def inject_running_section_headers(
    text: str,
    document_context: DocumentContext,
) -> str:
    """补齐跨页工序页眉：某页缺了 Phase 1 标题、且同一标题在相邻页（上一页或下一页）也被
    Phase 1 记录（说明是横跨多页的运行页眉，如工序工艺规程标题），则在该页顶部补上该标题。

    用「相邻页也有」判定运行页眉，既能补续页，也能补段落首页 VLM 漏写的情形，同时避免误注入
    只在单页出现的一次性正文标题；TOC 页与 flowchart_section 跳过；已存在（含 VLM 加空格的
    变体）则不重复注入。
    """
    if not document_context.page_structures:
        return text

    _page_marker_re = re.compile(r'(<!-- page: (\d+) -->)')
    parts = _page_marker_re.split(text)
    result: list[str] = [parts[0]]

    i = 1
    while i < len(parts) - 2:
        full_marker = parts[i]
        pno = int(parts[i + 1])
        content = parts[i + 2]

        ps = document_context.page_structures.get(pno)
        prev_ps = document_context.page_structures.get(pno - 1)
        next_ps = document_context.page_structures.get(pno + 1)
        if ps is None or ps.is_toc_page or (prev_ps is None and next_ps is None):
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        adjacent_keys = {
            _heading_match_key(h.text)
            for adj in (prev_ps, next_ps) if adj is not None
            for h in adj.headings + adj.appendix_headings
        }
        existing: set[str] = set()
        in_code = False
        for line in content.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
            if in_code:
                continue
            m = _HEADING_LINE_FOR_CORRECTION_RE.match(line)
            if m:
                existing.add(_heading_match_key(m.group(2).strip()))

        to_inject = []
        for h in ps.headings + ps.appendix_headings:
            if h.type == 'flowchart_section':
                continue
            key = _heading_match_key(h.text)
            if not key or key not in adjacent_keys or key in existing:
                continue
            key_long = len(key) >= _MIN_HEADING_CHARS_FOR_FUZZY
            fuzzy_hit = key_long and any(
                (key in e or e in key)
                for e in existing
                if len(e) >= _MIN_HEADING_CHARS_FOR_FUZZY
            )
            if fuzzy_hit:
                continue
            to_inject.append(h)
            existing.add(key)

        if to_inject:
            block = '\n'.join('#' * h.level + ' ' + h.text for h in to_inject)
            lead = re.match(r'^(\n*)', content).group(1)
            content = lead + block + '\n' + content[len(lead):]

        result.append(full_marker)
        result.append(content)
        i += 3

    if i < len(parts):
        result.extend(parts[i:])
    return ''.join(result)


_LIST_NUMBER_PREFIX_RE = re.compile(r'^(?:[-*]|\d+[.、．)])\s+')
_BULLET_PREFIX_RE = re.compile(r'^[-*]\s+')


def _strip_list_number_prefix(s: str) -> str:
    return _LIST_NUMBER_PREFIX_RE.sub('', s).strip()


def _match_keys(text: str) -> set[str]:
    """匹配键集合：去空白/大小写归一 + 去列表编号前缀 + 容忍脱敏占位(XXX)。"""
    base = _heading_match_key(text)
    stripped = _heading_match_key(_strip_list_number_prefix(text))
    keys = {base, stripped}
    keys |= {_REDACTION_RUN_RE.sub('', k) for k in (base, stripped)}
    return {k for k in keys if k}


def repromote_demoted_phase1_headings(
    text: str,
    document_context: DocumentContext,
) -> str:
    """以 Phase 1 为权威：把被启发式 pass 降成「纯文本/列表项」、但整行与本页 Phase 1
    标题精确吻合的行，重新提升为该 Phase 1 标题（用 Phase 1 的层级与文本）。

    只动「纯文本行」和「列表项行」，且要求整行（去掉列表/编号前缀后）与某条 Phase 1 标题
    精确匹配——因此不会误升真正的列表项。表N 的加粗（表格规则）、HTML 表格单元格、代码块、
    已存在为标题者、TOC 页、flowchart_section 一律不动。
    """
    if not document_context.page_structures:
        return text

    _page_marker_re = re.compile(r'(<!-- page: (\d+) -->)')
    parts = _page_marker_re.split(text)
    result: list[str] = [parts[0]]

    i = 1
    while i < len(parts) - 2:
        full_marker = parts[i]
        pno = int(parts[i + 1])
        content = parts[i + 2]

        ps = document_context.page_structures.get(pno)
        # 注意：不整页跳过 is_toc_page。真目录页 ps.headings 为空（Phase 1 规则：目录项不作
        # 标题），自然无可匹配；但被误判为目录页、却又记了 body_heading 的页（normalize_toc_blocks
        # 已把其标题降成列表项），仍需按 Phase 1 提升回标题。
        if ps is None:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        # Phase 1 标题键 → Heading（同时收录去前缀的键，兼容 '1. X' ↔ 'X'）
        p1map: dict[str, Heading] = {}
        for h in ps.headings + ps.appendix_headings:
            if h.type == 'flowchart_section':
                continue
            for k in _match_keys(h.text):
                p1map.setdefault(k, h)
        if not p1map:
            result.append(full_marker)
            result.append(content)
            i += 3
            continue

        # 已作为标题出现的，跳过（避免重复提升）
        existing: set[str] = set()
        in_code = False
        for line in content.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
            if in_code:
                continue
            m = _HEADING_LINE_FOR_CORRECTION_RE.match(line)
            if m:
                existing.add(_heading_match_key(m.group(2).strip()))

        out_lines: list[str] = []
        in_code = False
        in_html_table = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code = not in_code
                out_lines.append(line)
                continue
            if re.search(r'<table\b', stripped, re.IGNORECASE):
                in_html_table = True
            if '</table>' in stripped.lower():
                in_html_table = False
                out_lines.append(line)
                continue
            # 处理「纯文本」「列表项」「整行加粗」行；标题/表格/标记行放过
            is_list = bool(re.match(r'^[-*]\s+', stripped))
            bold_m = re.fullmatch(r'\*\*(.+?)\*\*', stripped)
            is_plain = bool(stripped) and stripped[0] not in ('#', '|', '<', '>', '`', '*')
            if in_code or in_html_table or not (is_list or is_plain or bold_m):
                out_lines.append(line)
                continue

            core = bold_m.group(1).strip() if bold_m else stripped
            candidates = _match_keys(core)
            matched = next(
                (p1map[k] for k in candidates if k in p1map and k not in existing),
                None,
            )
            if matched is not None:
                # 用行内可见文本（去项目符号 / 去加粗标记），保留脱敏处理，不还原 Phase 1 原文
                if bold_m:
                    display = core
                elif is_list:
                    display = _BULLET_PREFIX_RE.sub('', stripped)
                else:
                    display = stripped
                out_lines.append('#' * matched.level + ' ' + display)
                existing.add(_heading_match_key(display))
            else:
                out_lines.append(line)

        result.append(full_marker)
        result.append('\n'.join(out_lines))
        i += 3

    if i < len(parts):
        result.extend(parts[i:])
    return ''.join(result)


def postprocess_markdown(
    raw_markdown: str,
    file_title: str,
    document_context: DocumentContext,
    debug: bool = False,
) -> str:
    page_nos, page_contents = _split_pages(raw_markdown)
    page_contents = deduplicate_headers_footers(page_contents)
    rejoined = ''.join(
        f'<!-- page: {no} -->\n{content}'
        for no, content in zip(page_nos, page_contents, strict=False)
    )
    text = normalize_redacted_notsure(rejoined)
    text = normalize_toc_blocks(text, document_context.toc_pages)
    text = ensure_single_h1(text, file_title)
    text = demote_list_items_in_flowchart_sections(text)
    text = fix_flowchart_page_titles(text)
    text = normalize_appendix_headings(text)
    text = insert_appendix_parent_nodes(text)
    text = demote_figure_formula_headings(text)
    text = validate_and_annotate_mermaid(text)
    text = repair_unclosed_code_fences_at_page_markers(text)
    text = demote_headings_in_html_table_cells(text)
    text = demote_toc_style_headings(text)
    text = strip_output_noise(text)
    text = repair_unclosed_html_tables(text)
    text = inject_missing_headings_from_outline(text, document_context)
    text = inject_running_section_headers(text, document_context)
    text = repromote_demoted_phase1_headings(text, document_context)
    text = correct_heading_levels_from_outline(text, document_context)
    text = filter_unmatched_p2_headings(text, document_context)
    if not debug:
        text = re.sub(r'<!-- page: \d+ -->\n?', '', text)
    return text

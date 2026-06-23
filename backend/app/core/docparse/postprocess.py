from __future__ import annotations

import re
from collections import Counter
from collections import defaultdict

from .models import DocumentContext
from .utils import strip_notsure, normalize_heading_text
from .pdf_extractor import level_for_number
from .heading_rules import (
    REDACTION_NOTSURE_BLOCK_RE,
    MALFORMED_NOTSURE_CLOSE_RE,
    SIGNATURE_FIELD_CONTEXT_RE,
    is_redaction_notsure_inner,
    BOLD_APPENDIX_TABLE_LINE_RE,
    BOLD_PLAIN_TABLE_LINE_RE,
    HEADING_LINE_RE,
    MIN_SECTION_LEVEL,
    appendix_table_level,
    plain_table_level,
    is_appendix_table_title,
    is_plain_table_title,
    is_figure_or_formula_title,
)
from .structure_enrich import FLOWCHART_REQUIRED_SECTIONS, STACK_EXCLUDED_HEADING_RE, is_stack_heading

_FLOWCHART_SECTION_ALT = '|'.join(re.escape(s) for s in FLOWCHART_REQUIRED_SECTIONS)
FLOWCHART_SECTION_HEADING_RE = re.compile(rf'^####\s+({_FLOWCHART_SECTION_ALT})\s*$')
NUMBERED_LIST_AS_HEADING_RE = re.compile(r'^(#{2,6})\s+(\d+[.．、])\s+(.+)$')
_SEQUENTIAL_HEADING_RE = re.compile(r'^(#{2,6})\s+(\d+)[.．、]\s*(.+)$')
_LIST_ITEM_MIN_RUN = 4  # 连续递增数字标题达到此数量时，整组降级为有序列表
FLOWCHART_META_HEADING_RE = re.compile(
    r'^(#{2,6})\s+(工艺流程图|生产工艺流程图|工序流程图)\s*$',
    re.IGNORECASE,
)
PROCESS_REGULATION_BOLD_RE = re.compile(r'^\*\*(.+?工艺规程)\*\*\s*$')
FLOWCHART_LABEL_BOLD_RE = re.compile(r'^\*\*流程图\*\*\s*$')

NUMBERED_HEADING_RE = re.compile(r'^(#{2,6})\s+(\d+(?:\.\d+)*)[、.．]?\s*(.+)$')

# 正文里未加 # 的层级标题（如「3、标题」「3.1 标题」）
PLAIN_DUNHAO_SECTION_RE = re.compile(r'^(\d+)[、]\s*(.+)$')
PLAIN_DOT_SUBSECTION_RE = re.compile(r'^(\d+(?:\.\d+)+)\s+(.+)$')
PLAIN_TRAILING_DOT_SUBSECTION_RE = re.compile(r'^(\d+(?:\.\d+)+)\.\s+(.+)$')
PLAIN_TOP_DOT_SECTION_RE = re.compile(r'^(\d+)[.．]\s+(.+)$')
PLAIN_CN_SECTION_RE = re.compile(r'^([一二三四五六七八九十]+)[、]\s*(.+)$')
HEADING_CN_MAJOR_RE = re.compile(r'^[一二三四五六七八九十百千]+[、]')
HEADING_ARABIC_SINGLE_RE = re.compile(r'^\d+[.．、:：\s]')
HEADING_ARABIC_MULTI_RE = re.compile(r'^\d+(?:\.\d+)+[.．、:：\s]')
HEADING_DOC_CHAPTER_RE = re.compile(r'^\d+[、.．]\s*')
MAX_PLAIN_HEADING_CHARS = 150

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
_MD_SEP_ROW_RE = re.compile(r'^\|(?:[ :]*-{3,}[ :]*\|)+\s*$')
_ARABIC_SECTION_PREFIX_RE = re.compile(r'^(\d+(?:\.\d+)*)(?:[、.．]|\s)')
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


def fix_markdown_table_header(text: str) -> str:
    """Prepend an empty header row when a Markdown table starts with a separator row."""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        if _MD_SEP_ROW_RE.match(stripped):
            prev = result[-1].strip() if result else ''
            is_prev_data_row = prev.startswith('|') and not _MD_SEP_ROW_RE.match(prev)
            if not is_prev_data_row:
                col_count = stripped.count('|') - 1
                empty_header = '| ' + ' | '.join([' '] * col_count) + ' |'
                result.append(empty_header)
        result.append(line)
    return '\n'.join(result)


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


_NUMBERED_CONTENT_RE = re.compile(r'^\d')


def demote_semicolon_sentence_headings(text: str) -> str:
    """Demote numbered heading lines whose content ends in '；' to plain text.

    Targets checklist/evaluation items VLM directly tagged as headings.
    '；'-ending is a strong signal for list items in Chinese evaluation docs.
    '。'-ending is left untouched to preserve 工艺规程 process-step headings.
    """
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
        if in_code:
            result.append(line)
            continue
        m = HEADING_LINE_RE.match(line)
        if m:
            content = m.group(2).strip()
            if content.endswith('；') and _NUMBERED_CONTENT_RE.match(content):
                line = content
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


def fix_heading_level_inversions(text: str) -> str:
    """Fix numbered child headings that are at same/shallower level than their parent.

    Two cases:
    - Arabic depth-1 (1.) directly under a Chinese major heading (一、) at the same level.
    - Arabic child (1.1) with level <= its parent Arabic heading (1.) level.
    """
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    arabic_depth_level: dict[int, int] = {}
    chinese_major_level: int | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        m = HEADING_LINE_RE.match(line)
        if not m:
            result.append(line)
            continue

        level = len(m.group(1))
        if level == 1:
            result.append(line)
            continue

        content = m.group(2).strip()

        if HEADING_CN_MAJOR_RE.match(content):
            chinese_major_level = level
            arabic_depth_level.clear()
            result.append(line)
            continue

        dm = _ARABIC_SECTION_PREFIX_RE.match(content)
        if dm:
            num_str = dm.group(1)
            depth = num_str.count('.') + 1

            # Arabic depth-1 directly under Chinese major at same/shallower level
            if depth == 1 and chinese_major_level is not None and level <= chinese_major_level:
                level = min(chinese_major_level + 1, 6)
                line = '#' * level + ' ' + content

            # Arabic child whose parent is at same/deeper level
            parent_depth = depth - 1
            if parent_depth > 0 and parent_depth in arabic_depth_level:
                parent_level = arabic_depth_level[parent_depth]
                if level <= parent_level:
                    level = min(parent_level + 1, 6)
                    line = '#' * level + ' ' + content

            arabic_depth_level[depth] = level
            for d in list(arabic_depth_level):
                if d > depth:
                    del arabic_depth_level[d]
        else:
            # Non-numbered, non-Chinese-major heading at H2/H3 resets context
            if level <= 3:
                arabic_depth_level.clear()
                chinese_major_level = None

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


def _try_promote_plain_numbered_line(stripped: str) -> str | None:
    if is_figure_or_formula_title(stripped):
        return None
    m = PLAIN_DUNHAO_SECTION_RE.match(stripped)
    if m:
        level = level_for_number(m.group(1))
        return '#' * level + ' ' + stripped
    m = PLAIN_TRAILING_DOT_SUBSECTION_RE.match(stripped)
    if m:
        level = level_for_number(m.group(1))
        return '#' * level + ' ' + stripped
    m = PLAIN_DOT_SUBSECTION_RE.match(stripped)
    if m:
        level = level_for_number(m.group(1))
        return '#' * level + ' ' + stripped
    m = PLAIN_TOP_DOT_SECTION_RE.match(stripped)
    if m:
        level = level_for_number(m.group(1))
        return '#' * level + ' ' + stripped
    m = PLAIN_CN_SECTION_RE.match(stripped)
    if m:
        return '## ' + stripped
    return None


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


def demote_sequential_list_headings(text: str) -> str:
    """将 VLM 误标为 Markdown 标题的连续递增数字要点降级为有序列表。

    检测条件：同一级别、序号从 1 开始连续递增、数量 >= _LIST_ITEM_MIN_RUN 的标题行。
    允许相邻两个标题之间夹杂正文内容行（如 '## 9. 使用原辅料：' 下面有续行）。
    不跨越代码块和页面分隔符（<!-- page: N -->）。
    """
    lines = text.split('\n')
    in_code = False
    to_demote: set[int] = set()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            in_code = not in_code
        if in_code:
            i += 1
            continue

        m = _SEQUENTIAL_HEADING_RE.match(line)
        if not m or int(m.group(2)) != 1:
            i += 1
            continue

        level = len(m.group(1))
        run: list[int] = [i]
        expected = 2
        j = i + 1

        while j < len(lines):
            lj = lines[j]
            if lj.startswith('```'):
                break
            if PAGE_MARKER_RE.match(lj.strip()):
                break
            mj = _SEQUENTIAL_HEADING_RE.match(lj)
            if mj and len(mj.group(1)) == level and int(mj.group(2)) == expected:
                run.append(j)
                expected += 1
                j += 1
                continue
            # 遇到同级或更高级的其他标题，终止
            hm = HEADING_LINE_RE.match(lj)
            if hm and len(hm.group(1)) <= level:
                break
            # 其余（续行、空行、表格、正文）跳过继续找
            j += 1

        if len(run) >= _LIST_ITEM_MIN_RUN:
            to_demote.update(run)

        i = run[-1] + 1 if run else i + 1

    if not to_demote:
        return text

    result: list[str] = []
    in_code = False
    for idx, line in enumerate(lines):
        if line.startswith('```'):
            in_code = not in_code
        if not in_code and idx in to_demote:
            mf = _SEQUENTIAL_HEADING_RE.match(line)
            if mf:
                result.append(f'{mf.group(2)}. {mf.group(3)}')
                continue
        result.append(line)
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


def promote_plain_numbered_headings(text: str) -> str:
    """将 VLM 漏标的「3、…」「3.1 …」等纯文本行提升为 Markdown 标题。"""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    in_flowchart = False
    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        in_flowchart = _track_flowchart_block(line, in_flowchart)
        if in_code or HEADING_LINE_RE.match(line):
            result.append(line)
            continue
        stripped = line.strip()
        if (
            in_flowchart
            or not stripped
            or stripped.startswith('|')
            or stripped.startswith(('- ', '* ', '+ ', '> '))
            or len(stripped) > MAX_PLAIN_HEADING_CHARS
            or (len(stripped) > 40 and _SENTENCE_PUNCT_RE.search(stripped))
        ):
            result.append(line)
            continue
        promoted = _try_promote_plain_numbered_line(stripped)
        result.append(promoted if promoted else line)
    return '\n'.join(result)


def relevel_headings_under_chinese_sections(text: str) -> str:
    """「一、」下的「1.」「1.1.」等降为 ### / ####，避免与中文大节同级为 ##。"""
    lines = text.split('\n')
    result: list[str] = []
    in_code = False
    under_chinese_major = False

    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        m = HEADING_LINE_RE.match(line)
        if m:
            level = len(m.group(1))
            content = m.group(2).strip()

            if HEADING_CN_MAJOR_RE.match(content):
                under_chinese_major = True
                result.append('## ' + content)
                continue

            if under_chinese_major and HEADING_ARABIC_MULTI_RE.match(content):
                result.append('#### ' + content)
                continue

            if under_chinese_major and HEADING_ARABIC_SINGLE_RE.match(content):
                result.append('### ' + content)
                continue

            if level == 2 and HEADING_DOC_CHAPTER_RE.match(content):
                under_chinese_major = False

            result.append(line)
            continue

        stripped = line.strip()
        if under_chinese_major and PLAIN_TRAILING_DOT_SUBSECTION_RE.match(stripped):
            result.append('#### ' + stripped)
            continue

        result.append(line)

    return '\n'.join(result)


def fix_numbered_heading_levels(text: str) -> str:
    lines = text.split('\n')
    result = []
    in_code = False
    for line in lines:
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if not in_code:
            m = NUMBERED_HEADING_RE.match(line)
            if m:
                content = line[len(m.group(1)):].lstrip()
                # Skip re-leveling if content looks like a sentence (evaluation items etc.)
                if not _SENTENCE_PUNCT_RE.search(content):
                    correct = level_for_number(m.group(2))
                    line = '#' * correct + ' ' + content
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
    text = re.sub(r'<NOTSURE>(?:(?!</NOTSURE>).)*$', '', text, flags=re.DOTALL)
    text = _strip_bare_redaction_placeholders(text)
    return text


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


def promote_table_title_headings(text: str) -> str:
    """附表与最深章节同级；表题在附表下 +1。"""
    lines = text.split('\n')
    result = []
    in_code = False
    section_level = MIN_SECTION_LEVEL
    last_appendix_level: int | None = None
    for line in lines:
        if PAGE_MARKER_RE.match(line.strip()):
            in_code = False
            result.append(line)
            continue
        if line.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        stripped = line.rstrip()
        m = BOLD_APPENDIX_TABLE_LINE_RE.match(stripped)
        if m:
            content = m.group(1).strip()
            lv = appendix_table_level(section_level)
            last_appendix_level = lv
            result.append('#' * lv + ' ' + content)
            continue
        m = BOLD_PLAIN_TABLE_LINE_RE.match(stripped)
        if m and not is_figure_or_formula_title(m.group(1)):
            content = m.group(1).strip()
            lv = plain_table_level(section_level, last_appendix_level)
            result.append('#' * lv + ' ' + content)
            continue
        hm = HEADING_LINE_RE.match(stripped)
        if hm:
            lv = len(hm.group(1))
            content = hm.group(2).strip()
            if is_appendix_table_title(content):
                want = appendix_table_level(section_level)
                last_appendix_level = want
                result.append('#' * want + ' ' + content)
                continue
            if is_plain_table_title(content):
                want = plain_table_level(section_level, last_appendix_level)
                result.append('#' * want + ' ' + content)
                continue
            if (
                not is_figure_or_formula_title(content)
                and is_stack_heading(content)
            ):
                section_level = lv
                if last_appendix_level is None or lv <= last_appendix_level:
                    last_appendix_level = None
            result.append(line)
            continue
        result.append(line)
    return '\n'.join(result)


def _get_boundary_lines(page_text: str, n: int = 3) -> list[str]:
    lines = [ln for ln in page_text.splitlines() if ln.strip()]
    return lines[:n] + (lines[-n:] if len(lines) > n else [])


def deduplicate_headers_footers(pages: list[str]) -> list[str]:
    count: dict[str, int] = {}
    for page_text in pages:
        seen: set[str] = set()
        for line in _get_boundary_lines(page_text):
            clean = strip_notsure(line).strip()
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


def normalize_markdown_heading_levels(
    text: str,
    phase1_canonical: dict[str, int] | None = None,
) -> str:
    """最终保险层：对最终 Markdown 中所有结构性标题做全局 level 归一化。

    规范 level 确定优先级：
    1. phase1_canonical（Phase 1 已经归一化过的 level）—— 最权威
    2. Markdown 中出现的最小 level（最浅优先）—— 兜底：Phase 2 普遍偏向输出较深级别
       （如 ### 而非 ##），最小值更可能是正确的浅层级。

    排除项：H1（文档总标题）、代码块内容、流程图五小节（#### 流程图/架构图信息 等）。
    """
    lines = text.split('\n')
    in_code = False

    # Pass 1: 统计各标题文本出现的 level 集合（用 min）
    min_levels: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _MD_HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        if level == 1:
            continue
        heading_text = m.group(2).strip()
        if FLOWCHART_SECTION_HEADING_RE.match(stripped):
            continue
        key = normalize_heading_text(heading_text)
        if key:
            min_levels[key] = min(min_levels.get(key, level), level)

    if not min_levels:
        return text

    # 规范 level：Phase 1 优先，否则取 markdown 最小 level
    canonical: dict[str, int] = {}
    for key, min_lv in min_levels.items():
        if phase1_canonical and key in phase1_canonical:
            canonical[key] = phase1_canonical[key]
        else:
            canonical[key] = min_lv

    # Pass 2: 重写
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        m = _MD_HEADING_RE.match(line)
        if not m:
            result.append(line)
            continue
        level = len(m.group(1))
        if level == 1 or FLOWCHART_SECTION_HEADING_RE.match(stripped):
            result.append(line)
            continue
        heading_text = m.group(2).strip()
        key = normalize_heading_text(heading_text)
        new_level = canonical.get(key, level)
        result.append(('#' * new_level + ' ' + heading_text) if new_level != level else line)

    return '\n'.join(result)


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
        depth = len(re.findall(r'<table\b', line, re.IGNORECASE)) - len(re.findall(r'</table>', line, re.IGNORECASE))
        i += 1
        while i < len(lines) and depth > 0:
            l = lines[i]
            depth += len(re.findall(r'<table\b', l, re.IGNORECASE)) - len(re.findall(r'</table>', l, re.IGNORECASE))
            table_buf.append(l)
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
                if h_key == line_key or (h_key in line_key and ratio > _PLAIN_TITLE_MIN_HEADING_RATIO):
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


def _find_p15_level(heading_text: str, p15_headings: list) -> int | None:
    """Find the Phase 1.5 level for a heading text using exact then fuzzy match."""
    norm_p2 = normalize_heading_text(heading_text).lower()
    if len(norm_p2) < _MIN_HEADING_CHARS_FOR_FUZZY:
        return None
    for h in p15_headings:
        norm_p1 = normalize_heading_text(h.text).lower()
        if norm_p2 == norm_p1:
            return h.level
    for h in p15_headings:
        norm_p1 = normalize_heading_text(h.text).lower()
        if len(norm_p1) < _MIN_HEADING_CHARS_FOR_FUZZY:
            continue
        if norm_p2 in norm_p1 or norm_p1 in norm_p2:
            return h.level
    return None


def correct_heading_levels_from_outline(
    text: str,
    document_context: DocumentContext,
    p1_canonical: dict[str, int] | None = None,
) -> str:
    """Correct Phase 2 heading levels to match Phase 1.5 outline data.

    Splits by <!-- page: N --> markers and for each page uses Phase 1.5
    headings to correct levels that Phase 2 under-assigned.
    Exclusions: H1, code blocks, STACK_EXCLUDED_HEADING_RE matches.

    When p1_canonical is provided (the global-min level map already used by
    normalize_markdown_heading_levels), that canonical level takes precedence
    over the per-page Phase 1 level.  This prevents the two passes from
    assigning inconsistent levels to the same heading text across pages.
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
                # When a global canonical level exists, use it so that this pass
                # and normalize_markdown_heading_levels agree on the same level
                # for headings that appear on multiple pages.
                key = normalize_heading_text(heading_text)
                if p1_canonical and key in p1_canonical:
                    p15_level = p1_canonical[key]
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


def postprocess_markdown(
    raw_markdown: str,
    file_title: str,
    document_context: DocumentContext,
    debug: bool = False,
) -> str:
    page_nos, page_contents = _split_pages(raw_markdown)
    page_contents = deduplicate_headers_footers(page_contents)
    rejoined = ''.join(
        f'<!-- page: {no} -->\n{content}' for no, content in zip(page_nos, page_contents)
    )
    text = normalize_redacted_notsure(rejoined)
    text = normalize_toc_blocks(text, document_context.toc_pages)
    text = ensure_single_h1(text, file_title)
    text = promote_plain_numbered_headings(text)
    text = fix_numbered_heading_levels(text)
    text = demote_sequential_list_headings(text)
    text = relevel_headings_under_chinese_sections(text)
    text = demote_list_items_in_flowchart_sections(text)
    text = fix_flowchart_page_titles(text)
    text = normalize_appendix_headings(text)
    text = insert_appendix_parent_nodes(text)
    text = promote_table_title_headings(text)
    text = demote_figure_formula_headings(text)
    text = validate_and_annotate_mermaid(text)
    text = demote_headings_in_html_table_cells(text)
    text = demote_semicolon_sentence_headings(text)
    text = demote_toc_style_headings(text)
    # 全局 level 归一化（最终保险层）：以 Phase 1 输出为锚点，修正 Phase 2 的随机偏差
    p1_canonical: dict[str, int] = {}
    for ps in document_context.page_structures.values():
        for h in ps.headings + ps.appendix_headings:
            key = normalize_heading_text(h.text)
            if key:
                p1_canonical.setdefault(key, h.level)
                if h.level < p1_canonical[key]:
                    p1_canonical[key] = h.level
    text = normalize_markdown_heading_levels(text, phase1_canonical=p1_canonical)
    text = fix_heading_level_inversions(text)
    text = strip_output_noise(text)
    text = repair_unclosed_html_tables(text)
    text = fix_markdown_table_header(text)
    text = inject_missing_headings_from_outline(text, document_context)
    text = correct_heading_levels_from_outline(text, document_context, p1_canonical=p1_canonical)
    text = filter_unmatched_p2_headings(text, document_context)
    if not debug:
        text = re.sub(r'<!-- page: \d+ -->\n?', '', text)
    return text

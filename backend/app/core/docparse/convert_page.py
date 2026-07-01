from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from .models import DocumentContext, PageContext, Heading, PageStructure
from .qwen_client import call_vlm
from .prompts import PAGE_MARKDOWN_SYSTEM_PROMPT, build_page_user_text
from .structure_enrich import (
    is_stack_heading,
    page_has_flowchart,
    flowchart_output_complete,
    match_process_section_for_page,
    merge_headings_unique,
    align_process_headings_to_page,
    FLOWCHART_REQUIRED_SECTIONS,
)
from .heading_rules import (
    is_figure_or_formula_title,
    is_flowchart_meta_title,
    deepest_section_level,
    relevel_table_headings,
    PROCESS_SECTION_LINE_RE,
)
from .utils import strip_notsure

logger = logging.getLogger(__name__)

MAX_FLOWCHART_RETRIES = 1
MAX_DIAGRAM_RETRIES = 1
MAX_FIGURE_RETRIES = 1

IMAGE_PLACEHOLDER_RE = re.compile(
    r'<!--\s*[^>]*?(?:Image|image|图片|图示|Process\s*Flowchart)[^>]*-->'
    r'|\[(?:Image|IMAGE|图片|图示)\]',
    re.IGNORECASE,
)
PROCESS_TABLE_HINT_RE = re.compile(r'工序[（(]步[）)]内容及要求')
DIAGRAM_DESCRIPTION_RE = re.compile(r'图片内容描述')
FIGURE_CAPTION_LINE_RE = re.compile(
    r'^\*\*(图\s*\d[\d\-－–]*|附图\s*\d[\d\-－–]*)',
    re.UNICODE,
)
FIGURE_CAPTION_HINT_RE = re.compile(r'(?<![流程])图\s*\d[\d\-－–]*', re.UNICODE)


def _has_process_regulation_heading(known: list[Heading], page_raw_text: str) -> bool:
    if PROCESS_SECTION_LINE_RE.search(page_raw_text[:800]):
        return True
    return any(
        '工艺规程' in h.text and re.search(r'G\d{1,2}', h.text)
        for h in known
    )


def _page_has_figure_content(ps: PageStructure, page_raw_text: str) -> bool:
    if any(r.type == 'figure' for r in ps.regions):
        return True
    return bool(FIGURE_CAPTION_HINT_RE.search(page_raw_text[:2500]))


def _split_table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _is_table_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(not c or re.fullmatch(r':?-{3,}:?', c.replace(' ', '')) for c in cells)


class _HtmlTableRowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == 'tr':
            self._current_row = []
        elif tag in ('td', 'th') and self._current_row is not None:
            self._current_cell = []
        elif tag == 'br' and self._current_cell is not None:
            self._current_cell.append('\n')

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ('td', 'th') and self._current_row is not None and self._current_cell is not None:
            self._current_row.append(''.join(self._current_cell).strip())
            self._current_cell = None
        elif tag == 'tr' and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None
            self._current_cell = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _html_table_rows(markdown: str) -> list[list[str]]:
    parser = _HtmlTableRowParser()
    parser.feed(markdown)
    parser.close()
    return parser.rows


def _plain_cell(text: str) -> str:
    text = re.sub(r'<br\s*/?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def _is_arrow_only_flow(text: str) -> bool:
    plain = _plain_cell(text)
    return not plain or plain == '↓' or bool(re.fullmatch(r'↓+', plain))


def _is_step_flow_cell(text: str) -> bool:
    plain = _plain_cell(text)
    if _is_arrow_only_flow(text):
        return False
    return len(plain) >= 2


def _cell_has_diagram_description(cell: str) -> bool:
    if IMAGE_PLACEHOLDER_RE.search(cell):
        return False
    if DIAGRAM_DESCRIPTION_RE.search(cell) or '*(图片内容描述' in cell:
        return True
    plain = _plain_cell(cell)
    return len(plain) >= 12


def process_table_diagram_issues(markdown: str) -> list[str]:
    """Each process-table step row must have its own 图示 description."""
    if not PROCESS_TABLE_HINT_RE.search(markdown):
        return []
    html_rows = _html_table_rows(markdown)
    if html_rows:
        return _process_table_rows_diagram_issues(html_rows)

    pipe_rows: list[list[str] | None] = []
    for line in markdown.split('\n'):
        if '|' not in line:
            pipe_rows.append(None)
            continue
        pipe_rows.append(_split_table_cells(line))
    return _process_table_rows_diagram_issues(pipe_rows)


def _process_table_rows_diagram_issues(rows: list[list[str] | None]) -> list[str]:
    issues: list[str] = []
    diagram_col: int | None = None
    flow_col: int | None = None
    step_col: int | None = None
    active = False

    for cells in rows:
        if cells is None:
            active = False
            diagram_col = flow_col = step_col = None
            continue
        if '图示' in cells:
            diagram_col = cells.index('图示')
            flow_col = cells.index('流程') if '流程' in cells else None
            step_col = next(
                (i for i, c in enumerate(cells) if '工序' in c and '内容及要求' in c),
                step_col,
            )
            active = True
            continue
        if diagram_col is not None and any('工序' in c and '内容及要求' in c for c in cells):
            step_col = next(i for i, c in enumerate(cells) if '工序' in c and '内容及要求' in c)
            active = True
            continue
        if not active or diagram_col is None or _is_table_separator(cells):
            continue
        if len(cells) <= diagram_col:
            continue

        flow_cell = cells[flow_col] if flow_col is not None and flow_col < len(cells) else ''
        diagram_cell = cells[diagram_col]
        step_cell = cells[step_col] if step_col is not None and step_col < len(cells) else ''

        has_step = _is_step_flow_cell(flow_cell) or _is_step_flow_cell(step_cell)
        if not has_step:
            continue
        if _is_arrow_only_flow(flow_cell) and not _is_step_flow_cell(step_cell):
            continue
        if not _cell_has_diagram_description(diagram_cell):
            issues.append('missing_process_table_diagram')
    return issues


def _caption_has_nearby_description(lines: list[str], idx: int, max_nonempty: int = 3) -> bool:
    seen = 0
    for j in range(idx + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        seen += 1
        if seen > max_nonempty:
            break
        if DIAGRAM_DESCRIPTION_RE.search(stripped) or '*(图片内容描述' in stripped:
            return True
    return False


def figure_captions_missing_descriptions(markdown: str) -> list[str]:
    """Detect **图N …** captions without a description within 3 lines."""
    issues: list[str] = []
    lines = markdown.split('\n')
    in_code = False
    for i, line in enumerate(lines):
        if line.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        if FIGURE_CAPTION_LINE_RE.match(line.strip()) and not _caption_has_nearby_description(lines, i):
            issues.append('missing_figure_caption_description')
    return issues


def visual_descriptions_incomplete(
    markdown: str,
    *,
    check_process_table: bool = False,
    check_figure_captions: bool = False,
) -> list[str]:
    issues: list[str] = []
    if IMAGE_PLACEHOLDER_RE.search(markdown):
        issues.append('image_html_placeholder')
    if check_process_table:
        issues.extend(process_table_diagram_issues(markdown))
    if check_figure_captions:
        issues.extend(figure_captions_missing_descriptions(markdown))
    return list(dict.fromkeys(issues))


def diagram_descriptions_incomplete(markdown: str) -> list[str]:
    """Backward-compatible wrapper: process-table checks only."""
    return visual_descriptions_incomplete(markdown, check_process_table=True)


def build_page_context(
    document_context: DocumentContext,
    page_no: int,
    previous_page_tail: str | None,
    page_raw_text: str = '',
) -> PageContext:
    ps = document_context.page_structures.get(page_no, PageStructure(page_no=page_no))
    known = list(ps.headings) + list(ps.appendix_headings)
    known = merge_headings_unique(
        known,
        match_process_section_for_page(page_raw_text, document_context.process_sections),
    )
    known = align_process_headings_to_page(page_raw_text, known)
    section_level = deepest_section_level(
        known + list(document_context.current_heading_stack),
    )
    known = relevel_table_headings(known, section_level)
    is_fc = page_has_flowchart(ps, page_raw_text) and not ps.is_toc_page
    is_process_reg = _has_process_regulation_heading(known, page_raw_text) and not ps.is_toc_page
    has_figures = _page_has_figure_content(ps, page_raw_text) and not ps.is_toc_page
    if is_fc:
        fc_level = min(section_level + 1, 6)
        existing_texts = {h.text for h in known}
        for sec_name in FLOWCHART_REQUIRED_SECTIONS:
            if sec_name not in existing_texts:
                known.append(
                    Heading(text=sec_name, level=fc_level, type='flowchart_section')
                )
    return PageContext(
        file_title=document_context.file_title,
        page_no=page_no,
        total_pages=document_context.total_pages,
        is_toc_page=ps.is_toc_page,
        known_headings_on_this_page=known,
        current_heading_stack=list(document_context.current_heading_stack),
        current_appendix=document_context.current_appendix,
        page_regions=list(ps.regions),
        previous_page_tail=previous_page_tail,
        is_flowchart_page=is_fc,
        require_flowchart_structure=is_fc,
        require_diagram_descriptions=is_process_reg and not is_fc,
        require_figure_descriptions=has_figures and not is_fc and not is_process_reg,
    )


def extract_headings_from_markdown(markdown: str) -> list[Heading]:
    headings: list[Heading] = []
    in_code_block = False
    for line in markdown.split('\n'):
        if line.startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = re.match(r'^(#{2,6})\s+(.+)$', line)
        if not m:
            continue
        level = len(m.group(1))
        text = strip_notsure(m.group(2)).strip()
        if not is_stack_heading(text):
            continue
        if is_figure_or_formula_title(text) or is_flowchart_meta_title(text):
            continue
        htype = 'body_heading'
        if re.match(r'^(附件|附录|附表)', text):
            htype = 'appendix_heading'
        elif re.match(r'^表\s*\d', text):
            htype = 'table_title'
        headings.append(Heading(text=text, level=level, type=htype))
    return headings


def _build_visual_retry_hint(missing: list[str]) -> str:
    parts = ['【补全要求】上一版输出不完整。', f'缺少项：{", ".join(missing)}。']
    if 'missing_figure_caption_description' in missing:
        parts.append(
            '每个 **图N …** / **附图N …** 图题下方 3 行内必须写 **图片内容描述** 或 *(图片内容描述：…)*，'
            '描述图中结构、曲线、尺寸、标注等可见信息；禁止仅输出图题一行。'
        )
    if 'missing_process_table_diagram' in missing or 'image_html_placeholder' in missing:
        parts.append(
            '工艺规程工序表：每个有工序名的行的「图示」列都必须写 *(图片内容描述：…)*；'
            '禁止 <!-- Image --> 或空单元格；↓ 续行可空，但每个步骤行必须有描述。'
        )
    parts.append('请重新完整输出本页。')
    return '\n'.join(parts)


def convert_page_to_markdown(image_path: Path, page_context: PageContext) -> str:
    user_text = build_page_user_text(page_context)
    md = call_vlm(
        image_path=image_path,
        system_prompt=PAGE_MARKDOWN_SYSTEM_PROMPT,
        user_text=user_text,
    )
    if page_context.require_flowchart_structure:
        missing = flowchart_output_complete(md)
        for attempt in range(MAX_FLOWCHART_RETRIES):
            if not missing:
                break
            logger.warning(
                'Page %d flowchart output incomplete (missing %s), retry %d',
                page_context.page_no, missing, attempt + 1,
            )
            retry_hint = (
                '【补全要求】上一版输出不完整。'
                f'缺少项：{", ".join(missing)}。'
                '请重新输出：① 先输出本页真实章节标题（known_headings，用 ### 等，不得用流程图小节顶替）；'
                '② 再用 #### 输出五个流程图补充小节（流程图/架构图信息、节点列表、关系列表、'
                '流程链路总结、Mermaid 图）；③ 逐字转写页面上所有参数表/工艺表等 HTML 表格，'
                '不得因节点列表已写而省略表格正文。'
            )
            md = call_vlm(
                image_path=image_path,
                system_prompt=PAGE_MARKDOWN_SYSTEM_PROMPT,
                user_text=user_text + '\n' + retry_hint,
            )
            missing = flowchart_output_complete(md)
        if missing:
            logger.warning(
                'Page %d flowchart output still incomplete after retries (missing %s)',
                page_context.page_no, missing,
            )

    need_visual = page_context.require_diagram_descriptions or page_context.require_figure_descriptions
    if need_visual:
        max_retries = max(MAX_DIAGRAM_RETRIES, MAX_FIGURE_RETRIES)
        missing = visual_descriptions_incomplete(
            md,
            check_process_table=page_context.require_diagram_descriptions,
            check_figure_captions=page_context.require_figure_descriptions,
        )
        for attempt in range(max_retries):
            if not missing:
                break
            logger.warning(
                'Page %d visual descriptions incomplete (missing %s), retry %d',
                page_context.page_no, missing, attempt + 1,
            )
            md = call_vlm(
                image_path=image_path,
                system_prompt=PAGE_MARKDOWN_SYSTEM_PROMPT,
                user_text=user_text + '\n' + _build_visual_retry_hint(missing),
            )
            missing = visual_descriptions_incomplete(
                md,
                check_process_table=page_context.require_diagram_descriptions,
                check_figure_captions=page_context.require_figure_descriptions,
            )
        if missing:
            logger.warning(
                'Page %d visual descriptions still incomplete after retries (missing %s)',
                page_context.page_no, missing,
            )
    return md

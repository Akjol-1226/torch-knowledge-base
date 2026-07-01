"""Phase 2.5: targeted cross-page table-split repair via LLM.

Phase 2 converts each page independently, so a long table spanning pages becomes
several disconnected tables — the continuation page often loses empty columns and
mistakes its first data row for a header. This pass detects those page boundaries
(prev page ends with a table, next page starts directly with a table), sends only
the boundary region to an LLM (qwen3.7-max) for re-alignment, and splices the
result back. Everything else is left untouched. A faithfulness guard rejects any
repair that invents cell text or changes the column count.

与上游一致：把续表行**合并到上一页表格末尾**，跨页表因此变成一张完整、可渲染的 Markdown
表格。被并的续表行会归属上一页（那几行页码溯源略偏，是可接受的取舍——它们本就属于从上一页
开始的那张表）。注意这与"裸续表行另起一段"不同：裸数据行无表头/分隔行 Markdown 渲染不出表格。
（曾试过"就地修列 + 插页标记/补表头"以保页码，但与本 fork 的 postprocess 逐页 pass 冲突或产生
重复表头，最终回到上游 splice 的最小偏离做法。）
"""
from __future__ import annotations

import json
import logging
import re

from .config import get_config
from .qwen_client import call_llm

logger = logging.getLogger(__name__)

PHASE25_TABLE_SYSTEM_PROMPT = """\
# Role
你是 Markdown 表格修复工具，专门修复"同一张表格被分页切断"的问题。

# Input
用户发送一个 JSON 对象：
- reference_table_header: 上一页表格的表头（含分隔行），代表这张表正确的列结构
- continuation_fragment: 下一页开头那截表格——它可能丢了空列、把数据行误当表头、或多了一条分隔行

# 判断
首先判断 continuation_fragment 是否是 reference_table_header 那张表的**续表**
（同一张表被分页续写）：
- 若**不是同一张表**（列含义不同、是另一张独立表格）：只输出一行 `NOT_CONTINUATION`，不要别的。

# 若是续表，则重排 continuation_fragment：
1. 列数与 reference 完全一致；因数据为空而被丢掉的列，补成空单元格。
2. 删除被误当表头的内容：续表不重复表头，去掉多余的分隔行
（`| :--- | ... |`）和把数据行当表头的情况——所有行都应是数据行。
3. **每个已有单元格的文字、数字、编号一律原样保留，禁止新增、删除或修改任何文字内容**；
只允许补空列。

# 输出
- 只输出重排后的数据行，每行一条 Markdown 表格行（`| ... |`）。
- 不要表头，不要分隔行，不要代码块包裹，不要任何解释文字。
"""


def _is_table_row(line: str) -> bool:
    return line.strip().startswith('|')


_SEP_CELL_RE = re.compile(r'^(?::?-{3,}:?|:-{1,}:?)$')


def _is_separator_row(line: str) -> bool:
    """仅当每个非空单元格都是 `---`/`:---`/`:---:`（3+ 破折号）时才算分隔行。

    `| - | - |` 这种单破折号是"无值"占位数据行，不是分隔行。
    """
    s = line.strip()
    if not s.startswith('|'):
        return False
    cells = [c.strip() for c in _row_cells(s)]
    nonempty = [c for c in cells if c]
    return bool(nonempty) and all(_SEP_CELL_RE.match(c) for c in nonempty)


def _row_cells(line: str) -> list[str]:
    return [c for c in line.strip().strip('|').split('|')]


def _col_count(lines: list[str]) -> int:
    for line in lines:
        if _is_table_row(line):
            return len(_row_cells(line))
    return 0


def _norm_cell(cell: str) -> str:
    return re.sub(r'\s+', '', cell)


def _row_nonempty_cells(line: str) -> list[str]:
    """该行去空白后的非空单元格，按出现顺序。"""
    return [nc for nc in (_norm_cell(c) for c in _row_cells(line)) if nc]


def _data_rows(lines: list[str]) -> list[str]:
    return [line for line in lines if _is_table_row(line) and not _is_separator_row(line)]


def _faithful_rows(rows: list[str], fragment_lines: list[str]) -> bool:
    """忠实性校验：重排结果必须与 fragment 的数据行**逐行有序一一对应**。

    要求行数相等，且每行的非空单元格（去空白后、按顺序）完全相等。
    只允许插入空单元格（补回被丢的空列）。这样可挡住 LLM 漏行、清空单元格、
    调换列序、复制行、凭空造字——任何会改动或丢失内容的输出都会被拒绝。
    """
    frag = _data_rows(fragment_lines)
    if len(rows) != len(frag):
        return False
    return all(
        _row_nonempty_cells(r) == _row_nonempty_cells(f)
        for r, f in zip(rows, frag, strict=False)
    )


def _trailing_table_lines(body: str) -> list[str] | None:
    """返回 body 末尾连续的表格行（保持原顺序）；末尾非表格则 None。"""
    lines = body.split('\n')
    # 去掉尾部空行
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    if end == 0 or not _is_table_row(lines[end - 1]):
        return None
    start = end
    while start > 0 and _is_table_row(lines[start - 1]):
        start -= 1
    return lines[start:end]


def _leading_table_block(body: str) -> tuple[list[str], str] | None:
    """若 body（跳过开头空行后）直接以表格开头，返回 (表格行, 去掉该表格后的剩余 body)。

    只有"页面开头就是表格、上方没有标题/正文"才视为续表候选。
    """
    lines = body.split('\n')
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _is_table_row(lines[i]):
        return None
    start = i
    while i < len(lines) and _is_table_row(lines[i]):
        i += 1
    frag = lines[start:i]
    remaining = '\n'.join(lines[i:]).strip('\n')
    return frag, remaining


def _reference_header(ref_lines: list[str]) -> list[str]:
    """取表头（+分隔行）作为列结构参照，限制 token。"""
    if not ref_lines:
        return []
    if len(ref_lines) >= 2 and _is_separator_row(ref_lines[1]):
        return ref_lines[:2]
    return ref_lines[:1]


def _parse_repaired_rows(raw_out: str) -> list[str] | None:
    s = raw_out.strip()
    if not s:
        return None
    first_line = s.split('\n', 1)[0].upper()
    if 'NOT_CONTINUATION' in first_line:
        return None
    s = re.sub(r'^```[a-zA-Z]*\n?', '', s)
    s = re.sub(r'\n?```$', '', s)
    rows = [
        ln.rstrip()
        for ln in s.split('\n')
        if _is_table_row(ln) and not _is_separator_row(ln)
    ]
    return rows or None


def _call_repair(reference_lines: list[str], fragment_lines: list[str]) -> str:
    config = get_config()
    user_text = json.dumps(
        {
            'reference_table_header': '\n'.join(reference_lines),
            'continuation_fragment': '\n'.join(fragment_lines),
        },
        ensure_ascii=False,
    )
    return call_llm(
        system_prompt=PHASE25_TABLE_SYSTEM_PROMPT,
        user_text=user_text,
        model=config.phase25_model,
        max_tokens=config.phase25_max_tokens,
        timeout=config.phase25_timeout,
    )


def _empty_header_lines(col_count: int) -> list[str]:
    header = '| ' + ' | '.join([''] * col_count) + ' |'
    separator = '| ' + ' | '.join([':---'] * col_count) + ' |'
    return [header, separator]


def _headerless_block_repairable(block: list[str]) -> bool:
    if not block or any(_is_separator_row(line) for line in block):
        return False
    counts = [len(_row_cells(line)) for line in block]
    if len(set(counts)) != 1:
        return False
    col_count = counts[0]
    if col_count < 2:
        return False
    # Single short pipe rows are often prose with vertical bars. Wide rows are
    # usually extracted tables and need a header to render as Markdown.
    return len(block) >= 2 or col_count >= 4


def _repair_headerless_markdown_tables(body: str) -> tuple[str, int]:
    lines = body.split('\n')
    out: list[str] = []
    repaired = 0
    i = 0
    in_code = False

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('```'):
            in_code = not in_code
            out.append(line)
            i += 1
            continue
        if in_code or not _is_table_row(line):
            out.append(line)
            i += 1
            continue

        start = i
        while i < len(lines) and _is_table_row(lines[i]):
            i += 1
        block = lines[start:i]
        if _headerless_block_repairable(block):
            out.extend(_empty_header_lines(len(_row_cells(block[0]))))
            repaired += 1
        out.extend(block)

    return '\n'.join(out), repaired


def _rewrap(original: str, new_body: str) -> str:
    """用 new_body 替换 original 的正文，保留其首尾换行（页间距）。"""
    lead = original[: len(original) - len(original.lstrip('\n'))]
    trail = original[len(original.rstrip('\n')):]
    return lead + new_body + trail


_MARKER_SPLIT_RE = re.compile(r'(<!-- page: \d+ -->)')


def repair_format_with_llm(raw: str) -> str:
    """Phase 2.5 入口：定点修复跨页续表断列/拆表。

    修复后的续表行合并到上一页表尾，与上游一致。原文其余部分不动。
    """
    config = get_config()
    if not getattr(config, 'enable_phase25', False):
        return raw

    parts = _MARKER_SPLIT_RE.split(raw)
    # parts = [prefix, marker, content, marker, content, ...]
    content_indices = list(range(2, len(parts), 2))  # index of each content chunk
    if not content_indices:
        raw, headerless_repaired = _repair_headerless_markdown_tables(raw)
        if headerless_repaired:
            logger.info('Phase 2.5: added %d empty Markdown table headers', headerless_repaired)
        return raw

    # 当某页正文整段是续表、被并入上一页后清空时，记录重定向：
    # 后续边界应继续把内容并到真正承载表格的那一页（支持 3+ 页跨页表）。
    redirect: dict[int, int] = {}

    def _anchor(idx: int) -> int:
        while idx in redirect:
            idx = redirect[idx]
        return idx

    repaired = 0
    for a, b in zip(content_indices[:-1], content_indices[1:], strict=False):
        try:
            prev_idx = _anchor(a)
            prev_content = parts[prev_idx]
            curr_content = parts[b]
            prev_body = prev_content.strip('\n')
            curr_body = curr_content.strip('\n')

            # 代码围栏未闭合时不碰（避免把围栏内的 | 误当表格）
            if prev_body.count('```') % 2 or curr_body.count('```') % 2:
                continue

            ref = _trailing_table_lines(prev_body)
            if not ref:
                continue
            lead = _leading_table_block(curr_body)
            if not lead:
                continue
            frag_lines, curr_remaining = lead

            k = _col_count(ref)
            if k < 2:  # 单列"表格"多为误检，不值得调用
                continue

            out = _call_repair(_reference_header(ref), frag_lines)
            rows = _parse_repaired_rows(out)
            if not rows:
                continue

            if any(len(_row_cells(r)) != k for r in rows):
                logger.warning('Phase 2.5: 列数不一致，跳过本边界')
                continue
            if not _faithful_rows(rows, frag_lines):
                logger.warning('Phase 2.5: 行数/单元格文本不匹配，跳过本边界')
                continue

            # 合并：把对齐后的续表行接到上一页表格末尾 → 跨页表变成一张完整、可渲染的表
            # （与上游一致）。被并的续表行因此归属上一页（那几行页码溯源略偏，可接受的取舍）。
            new_prev_body = prev_body + '\n' + '\n'.join(rows)
            parts[prev_idx] = _rewrap(prev_content, new_prev_body)
            parts[b] = _rewrap(curr_content, curr_remaining)
            if not curr_remaining.strip():
                redirect[b] = prev_idx  # b 被掏空，后续内容继续并到 prev_idx
            repaired += 1
        except Exception as exc:
            logger.warning('Phase 2.5: 边界修复失败，保留原样: %s', exc)
            continue

    if repaired:
        logger.info('Phase 2.5: 合并了 %d 处跨页续表', repaired)
    headerless_repaired = 0
    for idx in content_indices:
        parts[idx], n = _repair_headerless_markdown_tables(parts[idx])
        headerless_repaired += n
    if headerless_repaired:
        logger.info('Phase 2.5: added %d empty Markdown table headers', headerless_repaired)
    return ''.join(parts)

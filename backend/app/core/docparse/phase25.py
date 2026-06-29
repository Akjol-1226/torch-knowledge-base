"""Phase 2.5: targeted cross-page table-split repair via LLM.

Phase 2 converts each page independently, so a long table spanning pages becomes
several disconnected tables — the continuation page often loses empty columns and
mistakes its first data row for a header. This pass detects those page boundaries
(prev page ends with a table, next page starts directly with a table), sends only
the boundary region to an LLM (qwen3.7-max) for re-alignment, and splices the
result back. Everything else is left untouched. A faithfulness guard rejects any
repair that invents cell text or changes the column count.

[torch 改动] 与上游不同：不把续表行搬到上一页、也不插页标记，而是**在续表自己那页就地
对齐**（补回被丢的空列、删掉被误当表头的行/多余分隔行）。理由：本 fork 的 postprocess 是
一整套"按页标记切块、逐页补页眉/改标题"的 pass（上游没有），若把续表行搬成上一页内容、或
在表格行间插页标记，会让那截行被逐页 pass 当成新页处理（如把工序页眉注入到表格中间，反而
把表劈开）。就地修列则：续表行留在原页 → pagemap 页码归属天然正确（溯源不丢）；与上游的
"补页眉(inject_running_section_headers)"互补而非冲突——它给该页补 `## 工序`，本 pass 把其下
那截表的列修对。参照列结构仍取自上一页表尾。
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
首先判断 continuation_fragment 是否是 reference_table_header 那张表的**续表**（同一张表被分页续写）：
- 若**不是同一张表**（列含义不同、是另一张独立表格）：只输出一行 `NOT_CONTINUATION`，不要别的。

# 若是续表，则重排 continuation_fragment：
1. 列数与 reference 完全一致；因数据为空而被丢掉的列，补成空单元格。
2. 删除被误当表头的内容：续表不重复表头，去掉多余的分隔行（`| :--- | ... |`）和把数据行当表头的情况——所有行都应是数据行。
3. **每个已有单元格的文字、数字、编号一律原样保留，禁止新增、删除或修改任何文字内容**；只允许补空列。

# 输出
- 只输出重排后的数据行，每行一条 Markdown 表格行（`| ... |`）。
- 不要表头，不要分隔行，不要代码块包裹，不要任何解释文字。
"""


def _is_table_row(line: str) -> bool:
    return line.strip().startswith('|')


_SEP_CELL_RE = re.compile(r'^:?-{3,}:?$')


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
    return [l for l in lines if _is_table_row(l) and not _is_separator_row(l)]


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
        for r, f in zip(rows, frag)
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


def _rewrap(original: str, new_body: str) -> str:
    """用 new_body 替换 original 的正文，保留其首尾换行（页间距）。"""
    lead = original[: len(original) - len(original.lstrip('\n'))]
    trail = original[len(original.rstrip('\n')):]
    return lead + new_body + trail


_MARKER_SPLIT_RE = re.compile(r'(<!-- page: \d+ -->)')


def repair_format_with_llm(raw: str) -> str:
    """Phase 2.5 入口：定点修复跨页续表断列/拆表（就地修列，不搬页、不插标记）。原文其余部分不动。"""
    config = get_config()
    if not getattr(config, 'enable_phase25', False):
        return raw

    parts = _MARKER_SPLIT_RE.split(raw)
    # parts = [prefix, marker, content, marker, content, ...]
    content_indices = list(range(2, len(parts), 2))  # index of each content chunk
    if len(content_indices) < 2:
        return raw

    repaired = 0
    for a, b in zip(content_indices[:-1], content_indices[1:]):
        try:
            # prev 取紧邻的上一页内容块。多页跨表时，上一页若已被本 pass 就地修过，其表尾即对齐后的
            # 行 → 作为本页参照仍正确（无需 redirect：行始终留在原页，前一页内容块不会被掏空）。
            prev_content = parts[a]
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

            # [torch 改动] 就地修列：把本页那截错位续表替换为对齐后的行（去表头/分隔行、补空列），
            # 行留在原页 → pagemap 页码归属不变、不与 postprocess 逐页 pass 冲突。其余正文接其后。
            new_curr_body = '\n'.join(rows)
            if curr_remaining:
                new_curr_body += '\n' + curr_remaining
            parts[b] = _rewrap(curr_content, new_curr_body)
            repaired += 1
        except Exception as exc:
            logger.warning('Phase 2.5: 边界修复失败，保留原样: %s', exc)
            continue

    if repaired:
        logger.info('Phase 2.5: 就地修复了 %d 处跨页续表', repaired)
    return ''.join(parts)

from __future__ import annotations

import re


TOC_KEYWORD_RE = re.compile(r'目\s*录|Contents', re.IGNORECASE)
TOC_LINE_RE = re.compile(r'.+[.·…]{3,}\s*\d+\s*$')


def level_for_number(number: str) -> int:
    """根据编号层级计算 Markdown 标题等级（仅供 postprocess 阶段使用）。"""
    depth = number.count('.') + 1
    return min(depth + 1, 6)


def _detect_toc_page(text: str) -> bool:
    head = text[:200]
    if TOC_KEYWORD_RE.search(head):
        return True
    lines = [ln for ln in text.split('\n') if ln.strip()]
    if not lines:
        return False
    toc_count = sum(1 for ln in lines if TOC_LINE_RE.match(ln.strip()))
    return toc_count / len(lines) >= 0.6

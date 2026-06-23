"""notsure 审核闸门：从解析后的 md 提取 <notsure>…</notsure> 段落供人工审核。

DocVisionMD 解析 PDF 时把不确定的内容（模糊参数、看不清的单元格等）用
<notsure>…</notsure> 框起来。这些内容不能直接进知识库——必须人工确认/修正后
才建树入库。本模块负责把 notsure 段连同上下文、位置提取成待审核条目；审核动作
（确认/修正）与写回由 review 流程（切片 3）消费。
"""

import re

_NOTSURE = re.compile(r"<notsure>(.*?)</notsure>", re.S)


def extract_notsure(md_text: str, context_chars: int = 80) -> list[dict]:
    """提取 md 中所有 notsure 段，返回待审核条目列表。

    每条含：序号 id、不确定内容 content、前后文 context_before/after（便于人判断）、
    所在行号 line、原文字符区间 span（便于审核修正后精确写回）。
    """
    items: list[dict] = []
    for i, m in enumerate(_NOTSURE.finditer(md_text), start=1):
        start, end = m.start(), m.end()
        items.append(
            {
                "id": i,
                "content": m.group(1).strip(),
                "context_before": md_text[max(0, start - context_chars) : start],
                "context_after": md_text[end : end + context_chars],
                "line": md_text.count("\n", 0, start) + 1,
                "span": [start, end],
            }
        )
    return items


def count_notsure(md_text: str) -> int:
    """统计 md 中 notsure 段数量（用于任务卡片上显示"待审 N 处"）。"""
    return len(_NOTSURE.findall(md_text))

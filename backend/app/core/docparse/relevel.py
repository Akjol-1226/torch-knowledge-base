from __future__ import annotations

import json
import logging
import re

from .config import get_config
from .models import DocumentContext, Heading
from .qwen_client import call_llm

logger = logging.getLogger(__name__)

RELEVEL_SYSTEM_PROMPT = """\
# Role
你是文档标题层级梳理工具。

# Input
用户发送一个 JSON 对象，包含：
- file_title: 文档标题
- headings: 按页码顺序排列的所有标题，每条含 page（页码）、text（标题文字）、level（当前层级）

# 背景
level 是各页**独立识别**的初步结果，跨页可能不一致——尤其：一个标题若单独出现在某页的开头，
往往会被低估层级（因为那一页看不到它的上级章节）。

# Goal
通读全文标题，结合每条的文字、编号线索、所在章节、前后邻居，**自行判断**每条在整篇文档中
合理的绝对 level，使其构成一棵自洽的层级树。

# 判断要点（综合判断，不是机械套编号）
- level 取值 2–6，禁止使用 level=1（H1 由系统保留给文档总标题）。
- 同类工序规程标题（「G01 …工序工艺规程」「G02 …」编号序列）或「第一章/第二章」「一、二、」等
  章节序列，在文档中彼此对等，必须赋予相同 level。
- 「附表 N」「附件 N」属于其所在章节的直接子项：往前找最近的章节标题，level = 该章节 + 1，
  绝不与父章节同级。
- 正文小节（「本工序要点：」「注意事项：」「一、xxx」「1. xxx」等）的 level 必须大于所属章节。
- 编号是局部线索而非铁律：x.y 通常在 x 之下、x.y.z 更深，但「1.1」不一定是顶层——要结合它前面
  真正的父章节判断它的绝对深度。同一节下并列的小节通常应同级；某条明显比并列邻居突兀的，重新斟酌。
- 被页首"标浅"的标题，请结合它在全文中的位置与上下文，还原它真正的深度。

# 输出格式约束
1. 只输出 JSON 数组，格式与输入 headings 完全一致（每项含 page、text、level）。
2. 禁止用代码块包裹；直接输出裸数组。
3. 输出条目数量必须与输入完全一致，且顺序不变。
4. 禁止修改任何条目的 text 或 page 字段。
"""


def _collect_items(document_context: DocumentContext) -> list[tuple[int, Heading]]:
    """返回 [(page_no, heading), ...] 排除目录页，按页码顺序。"""
    result: list[tuple[int, Heading]] = []
    for page_no in sorted(document_context.page_structures.keys()):
        ps = document_context.page_structures[page_no]
        if ps.is_toc_page:
            continue
        for h in ps.headings + ps.appendix_headings:
            result.append((page_no, h))
    return result


def _parse_corrections(raw: str, collected: list[tuple[int, Heading]]) -> list[int | None]:
    """解析 LLM 输出，按索引返回修正后的 level（None 表示保留原值）。"""
    raw = raw.strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        raise ValueError('relevel: 响应中未找到 JSON 数组')

    items = json.loads(match.group(0))

    if len(items) != len(collected):
        logger.warning(
            'relevel: 输出条目数 %d 与输入 %d 不符，跳过本次修正',
            len(items), len(collected),
        )
        return [None] * len(collected)

    result: list[int | None] = []
    for i, (item, (_page_no, h)) in enumerate(zip(items, collected)):
        # 按文本匹配即可；LLM 偶尔把页码记串（如 153→154），文本相符仍视为同一条，容忍页码漂移
        if item.get('text') != h.text:
            logger.warning(
                'relevel: 第 %d 条文本不匹配（期望 "%s"，实得 "%s"），保留原值',
                i, h.text, item.get('text'),
            )
            result.append(None)
            continue

        new_level = item.get('level')
        if not isinstance(new_level, int) or not (2 <= new_level <= 6):
            result.append(None)
        else:
            result.append(new_level)

    return result


def relevel_headings_with_llm(document_context: DocumentContext) -> None:
    """Phase 1.5：用 LLM 全局梳理标题层次，修正逐页提取的层级不一致。原地修改。"""
    config = get_config()
    if not config.enable_relevel:
        return

    collected = _collect_items(document_context)
    if not collected:
        return

    input_items = [
        {'page': page_no, 'text': h.text, 'level': h.level}
        for page_no, h in collected
    ]

    user_text = json.dumps(
        {'file_title': document_context.file_title, 'headings': input_items},
        ensure_ascii=False,
    )

    try:
        raw = call_llm(
            system_prompt=RELEVEL_SYSTEM_PROMPT,
            user_text=user_text,
            model=config.relevel_model,
            max_tokens=config.relevel_max_tokens,
            timeout=config.relevel_timeout,
        )
        corrections = _parse_corrections(raw, collected)
    except Exception as exc:
        logger.warning('relevel_headings_with_llm 失败，保留原始层级: %s', exc)
        return

    changed = 0
    for (_, h), new_level in zip(collected, corrections):
        if new_level is not None and new_level != h.level:
            h.level = new_level
            changed += 1

    logger.info('relevel_headings_with_llm: 修正 %d / %d 条标题层级', changed, len(collected))

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

# Goal
检查并修正标题的 level 字段，使文档层次结构合理一致。

# Rules（严格执行）

## 输出格式约束
1. 只输出 JSON 数组，格式与输入 headings 完全一致（每项含 page、text、level）。
2. 禁止用代码块包裹；直接输出裸数组。
3. 输出条目数量必须与输入完全一致，且顺序不变。
4. 禁止修改任何条目的 text 或 page 字段。
5. level 取值范围 2–6，禁止使用 level=1（H1 由系统保留给文档总标题）。

## 层级修正规则

### 同类工序章节平级
同类工序规程标题（如「G01 xxx工序工艺规程」「G02 xxx工序工艺规程」等编号序列，
或「第一章」「第二章」等章节序列）在文档中必然是对等关系，必须赋予相同的 level。

### 附表/附件归属
- 「附表 N」「附件 N」属于其所在工序章节的直接子项。
- 所属章节：从该附表/附件往前查找最近的工序章节标题（如 G01、G02 等）。
- 附表/附件的 level = 所属工序章节 level + 1。
- 若附表/附件当前 level 与所属章节相同（即误置为平级），必须降一级。

### 正文小节归属
- 形如「本工序要点：」「注意事项：」「一、xxx」「1. xxx」等正文小节，
  若被标记为标题，其 level 必须大于所属工序章节的 level。
- 与工序章节同 level 的正文小节视为误赋，level += 1。

## 保守原则
- 若某条标题层级已经合理（如 level=3 的附表位于 level=2 的章节之下），不做修改。
- 只改有问题的条目，其余照原样输出。
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
    for i, (item, (page_no, h)) in enumerate(zip(items, collected)):
        if item.get('text') != h.text or item.get('page') != page_no:
            logger.warning(
                'relevel: 第 %d 条不匹配（期望 p.%d "%s"，实得 p.%s "%s"），保留原值',
                i, page_no, h.text, item.get('page'), item.get('text'),
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

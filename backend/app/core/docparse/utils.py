from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import re
from pathlib import Path

if TYPE_CHECKING:
    from .models import Heading


def get_file_title(pdf_path: str) -> str:
    return Path(pdf_path).stem


def strip_notsure(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'<NOTSURE>(.*?)</NOTSURE>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'</?NOTSURE>', '', text)
    return text


def extract_tail_text(markdown: str, max_chars: int = 300) -> Optional[str]:
    if not markdown:
        return None
    lines = markdown.splitlines()
    result: list[str] = []
    total = 0
    for line in reversed(lines):
        needed = len(line) + 1
        if total + needed > max_chars and result:
            break
        result.append(line)
        total += needed
    return "\n".join(reversed(result)) or None


def update_heading_stack(stack: list[Heading], new_headings: list[Heading]) -> list[Heading]:
    stack = list(stack)
    for heading in new_headings:
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
    return stack


def normalize_heading_text(text: str) -> str:
    text = text.strip()
    text = text.replace('：', ':').replace('（', '(').replace('）', ')')
    # 全角数字 → 半角
    text = text.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    # 全角字母 → 半角
    text = text.translate(str.maketrans(
        'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ',
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
    ))
    # 全角句点 → 半角
    text = text.replace('．', '.')
    # 间隔号去除
    text = text.replace('·', '')
    # en dash / em dash 统一为 -
    text = text.replace('–', '-').replace('—', '-')
    text = re.sub(r'\s+', ' ', text)
    return text

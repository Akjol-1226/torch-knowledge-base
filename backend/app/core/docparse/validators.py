from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

from .utils import strip_notsure

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def validate_notsure_tags(text: str) -> list[str]:
    errors: list[str] = []
    open_count = len(re.findall(r'<NOTSURE>', text, re.IGNORECASE))
    close_count = len(re.findall(r'</NOTSURE>', text, re.IGNORECASE))
    if open_count != close_count:
        errors.append(f'NOTSURE tag count mismatch: open={open_count}, close={close_count}')
    inner = re.sub(r'<NOTSURE>(.*?)</NOTSURE>', r'\1', text, flags=re.DOTALL)
    if '<NOTSURE>' in inner:
        errors.append('Nested NOTSURE tags are not allowed.')
    for m in re.finditer(r'</?[Nn][Oo][Tt][Ss][Uu][Rr][Ee][^>]*>', text):
        if m.group() not in ('<NOTSURE>', '</NOTSURE>'):
            errors.append(f'Invalid NOTSURE tag variant found: {m.group()!r}')
            break
    return errors


def validate_markdown(text: str) -> ValidationReport:
    report = ValidationReport()
    h1_lines = [ln for ln in text.split('\n') if re.match(r'^# [^#]', ln)]
    if len(h1_lines) != 1:
        report.errors.append(f'H1 count expected 1, got {len(h1_lines)}')
    report.errors.extend(validate_notsure_tags(text))
    mermaid_re = re.compile(r'```mermaid\n(.*?)```', re.DOTALL)
    valid_decl = re.compile(
        r'^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie)',
        re.IGNORECASE,
    )
    for block in mermaid_re.finditer(text):
        first = block.group(1).lstrip('\n').split('\n')[0].strip()
        if not valid_decl.match(first):
            report.errors.append(f'Mermaid block missing valid declaration: {first!r}')
    return report


def _flatten_nested_notsure(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = re.sub(
            r'<NOTSURE>\s*<NOTSURE>(.*?)</NOTSURE>\s*</NOTSURE>',
            r'<NOTSURE>\1</NOTSURE>',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return text


def repair_notsure_tags(text: str) -> str:
    """Fix malformed, nested, or unclosed NOTSURE tags while preserving OCR content."""
    from .heading_rules import MALFORMED_NOTSURE_CLOSE_RE

    text = MALFORMED_NOTSURE_CLOSE_RE.sub('</NOTSURE>', text)
    text = _flatten_nested_notsure(text)
    open_count = len(re.findall(r'<NOTSURE>', text, re.IGNORECASE))
    close_count = len(re.findall(r'</NOTSURE>', text, re.IGNORECASE))
    if open_count > close_count:
        text = re.sub(
            r'<NOTSURE>(?:(?!</NOTSURE>).)*$',
            lambda m: m.group(0) + '</NOTSURE>',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return text


def repair_markdown(text: str, report: ValidationReport) -> str:
    from .postprocess import validate_and_annotate_mermaid

    if any('NOTSURE' in e for e in report.errors):
        text = repair_notsure_tags(text)
        logger.warning('NOTSURE tags repaired automatically')

    if any('H1' in e for e in report.errors):
        lines = text.split('\n')
        h1_seen = False
        fixed = []
        for line in lines:
            if re.match(r'^# [^#]', line):
                if h1_seen:
                    line = '#' + line
                else:
                    h1_seen = True
            fixed.append(line)
        text = '\n'.join(fixed)

    if any('Mermaid' in e for e in report.errors):
        text = validate_and_annotate_mermaid(text)

    return text

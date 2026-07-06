from __future__ import annotations

import hashlib
import html
from difflib import SequenceMatcher


def _normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def text_hash(text: str) -> str:
    payload = _normalize_text(text).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def make_diff_html(before: str, after: str) -> str:
    before_lines = _normalize_text(before).splitlines()
    after_lines = _normalize_text(after).splitlines()
    matcher = SequenceMatcher(a=before_lines, b=after_lines)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.extend(html.escape(x) for x in before_lines[i1:i2])
        elif tag == "delete":
            parts.extend(f"<del>{html.escape(x)}</del>" for x in before_lines[i1:i2])
        elif tag == "insert":
            parts.extend(f"<ins>{html.escape(x)}</ins>" for x in after_lines[j1:j2])
        elif tag == "replace":
            parts.extend(f"<del>{html.escape(x)}</del>" for x in before_lines[i1:i2])
            parts.extend(f"<ins>{html.escape(x)}</ins>" for x in after_lines[j1:j2])
    return "<br>".join(parts)


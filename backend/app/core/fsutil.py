"""文件存储的原子写与安全名工具（v0 用文件存储，多入口并发下需防截断/穿越）。

原子写：先写同目录临时文件再 os.replace（同卷上是原子 rename），避免读到写一半的 JSON。
安全名：把用户可控的 kb / 文件名 stem 收敛成 [A-Za-z0-9_-]，防路径穿越与意外字符。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def safe_name(s: str | None, *, default: str = "_", maxlen: int = 64) -> str:
    """把任意字符串收敛成安全文件名片段：仅留字母数字与 - _，截断 maxlen，空则用 default。"""
    cleaned = "".join(c for c in (s or "") if c.isalnum() or c in "-_")[:maxlen]
    return cleaned or default


def write_text_atomic(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子写文本：同目录临时文件 + os.replace。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, path)  # 原子发布
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: str | Path, data, *, indent: int | None = None) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent))

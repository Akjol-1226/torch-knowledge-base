from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.fsutil import safe_name


@dataclass(frozen=True)
class ParsedDocument:
    doc_id: str
    doc_name: str
    kb: str
    markdown: str
    workspace: dict[str, Any]


def _workspace_path(doc_id: str) -> Path:
    return get_settings().data_dir / "workspace" / f"{safe_name(doc_id)}.json"


def read_workspace_doc(doc_id: str) -> dict[str, Any] | None:
    path = _workspace_path(doc_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def read_parsed_document(doc_id: str) -> ParsedDocument | None:
    workspace = read_workspace_doc(doc_id)
    if workspace is None:
        return None

    md_root = (get_settings().data_dir / "md").resolve()
    raw_path = workspace.get("path") or ""
    try:
        md_path = Path(raw_path).resolve()
    except (OSError, RuntimeError):
        return None
    if not md_path.exists() or not _is_under(md_path, md_root):
        return None

    try:
        markdown = md_path.read_text(encoding="utf-8")
    except OSError:
        return None

    return ParsedDocument(
        doc_id=doc_id,
        doc_name=str(workspace.get("doc_name", "")),
        kb=str(workspace.get("kb", "default")),
        markdown=markdown,
        workspace=workspace,
    )

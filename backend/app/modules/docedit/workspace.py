from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.fsutil import safe_name, write_json_atomic, write_text_atomic
from app.modules.docedit.diff import text_hash
from app.modules.docedit.service_exceptions import (
    DoceditBadRequest,
    DoceditConflict,
    DoceditNotFound,
    DoceditServiceUnavailable,
)
from app.modules.ingest.locks import INDEX_LOCK
from app.modules.ingest.tree_service import rebuild_from_workspace

_GENERIC_TITLE = re.compile(r"^(附表|附件|表|图)\s*\d*$")


@dataclass(slots=True)
class PublishResult:
    before_text: str
    after_text: str
    text_hash: str
    line_delta: int
    index_result: dict[str, Any]
    snapshot: ArtifactSnapshot


@dataclass(slots=True)
class ArtifactSnapshot:
    files: dict[Path, bytes | None]
    indexes_dir: Path | None


def parse_handle(handle: str) -> tuple[str, str]:
    if ":" not in handle:
        raise DoceditBadRequest("invalid node handle")
    doc_id, node_id = handle.split(":", 1)
    if not doc_id or not node_id:
        raise DoceditBadRequest("invalid node handle")
    return doc_id, node_id


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DoceditNotFound(f"document workspace not found: {path.name}") from exc


def load_workspace_doc(doc_id: str) -> dict[str, Any]:
    settings = get_settings()
    ws_path = settings.data_dir / "workspace" / f"{safe_name(doc_id)}.json"
    doc = _read_json(ws_path)
    if doc.get("id") != doc_id:
        raise DoceditConflict("workspace doc id mismatch")
    md_root = (settings.data_dir / "md").resolve()
    md_path = Path(doc.get("path", "")).resolve()
    if md_root != md_path and md_root not in md_path.parents:
        raise DoceditConflict("markdown path outside DATA_DIR/md")
    if not md_path.exists():
        raise DoceditNotFound("markdown file not found")
    return doc


def _walk(nodes: list[dict[str, Any]], ancestors: list[dict[str, Any]] | None = None):
    ancestors = ancestors or []
    for node in nodes:
        yield node, ancestors
        yield from _walk(node.get("nodes") or [], [*ancestors, node])


def find_node(doc: dict[str, Any], node_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for node, ancestors in _walk(doc.get("structure") or []):
        if node.get("node_id") == node_id:
            return node, ancestors
    raise DoceditNotFound("node not found")


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", "", title or "")


def _is_groupable_title(title: str) -> bool:
    normalized = _norm_title(title)
    return bool(normalized) and not _GENERIC_TITLE.match(normalized)


def _siblings_for_node(
    doc: dict[str, Any],
    ancestors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if ancestors:
        return list(ancestors[-1].get("nodes") or [])
    return list(doc.get("structure") or [])


def _is_merged_window_node(
    node: dict[str, Any],
    siblings: list[dict[str, Any]],
) -> bool:
    if (node.get("section") or {}).get("members"):
        return True
    title = (node.get("title") or "").strip()
    if not _is_groupable_title(title):
        return False
    try:
        index = next(
            i for i, item in enumerate(siblings)
            if item.get("node_id") == node.get("node_id")
        )
    except StopIteration:
        return False
    normalized = _norm_title(title)
    count = 1
    i = index - 1
    while i >= 0 and _norm_title(siblings[i].get("title") or "") == normalized:
        count += 1
        i -= 1
    i = index + 1
    while i < len(siblings) and _norm_title(siblings[i].get("title") or "") == normalized:
        count += 1
        i += 1
    return count > 1


def _nodes_by_line(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (node for node, _ in _walk(doc.get("structure") or [])),
        key=lambda n: int(n.get("line_num") or 0),
    )


def extract_body_text(node_text: str) -> str:
    lines = (node_text or "").split("\n")
    if lines and re.match(r"#{1,6}\s", lines[0].strip()):
        return "\n".join(lines[1:]).strip("\n")
    return (node_text or "").strip("\n")


def has_own_body_text(node_text: str) -> bool:
    return any(
        s and not re.match(r"#{1,6}\s", s)
        for s in (ln.strip() for ln in extract_body_text(node_text).split("\n"))
    )


def _body_has_heading(block_lines: list[str]) -> bool:
    return any(re.match(r"#{1,6}\s", line.strip()) for line in block_lines[1:])


def _normalize_block(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _snapshot_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _restore_file(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    tmp = path.parent / f".{path.name}.docedit_snapshot"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(path, tmp)
    return tmp


def _restore_dir(path: Path, snapshot: Path | None) -> None:
    shutil.rmtree(path, ignore_errors=True)
    if snapshot is not None and snapshot.exists():
        shutil.copytree(snapshot, path)
        shutil.rmtree(snapshot, ignore_errors=True)


def snapshot_live_artifacts(doc: dict[str, Any]) -> ArtifactSnapshot:
    settings = get_settings()
    md_path = Path(doc["path"])
    files = {
        md_path: _snapshot_file(md_path),
        Path(str(md_path) + ".pagemap.json"): _snapshot_file(Path(str(md_path) + ".pagemap.json")),
        Path(str(md_path) + ".ocr.json"): _snapshot_file(Path(str(md_path) + ".ocr.json")),
        settings.data_dir / "workspace" / f"{safe_name(doc['id'])}.json": _snapshot_file(
            settings.data_dir / "workspace" / f"{safe_name(doc['id'])}.json"
        ),
        settings.data_dir / "workspace" / "_meta.json": _snapshot_file(
            settings.data_dir / "workspace" / "_meta.json"
        ),
        settings.data_dir / "catalog" / "document_catalog.json": _snapshot_file(
            settings.data_dir / "catalog" / "document_catalog.json"
        ),
        settings.data_dir / "domain_dict_auto.txt": _snapshot_file(
            settings.data_dir / "domain_dict_auto.txt"
        ),
    }
    return ArtifactSnapshot(files=files, indexes_dir=_snapshot_dir(settings.data_dir / "indexes"))


def restore_live_artifacts(snapshot: ArtifactSnapshot) -> None:
    settings = get_settings()
    for path, data in snapshot.files.items():
        _restore_file(path, data)
    _restore_dir(settings.data_dir / "indexes", snapshot.indexes_dir)


def cleanup_live_artifact_snapshot(snapshot: ArtifactSnapshot | None) -> None:
    if snapshot is not None and snapshot.indexes_dir is not None:
        shutil.rmtree(snapshot.indexes_dir, ignore_errors=True)


def _shift_pagemap(md_path: Path, start_line_exclusive: int, line_delta: int) -> None:
    sidecar = Path(str(md_path) + ".pagemap.json")
    if not sidecar.exists() or not line_delta:
        return
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    shifted = [
        [line + line_delta if line >= start_line_exclusive else line, page]
        for line, page in data
    ]
    write_text_atomic(sidecar, json.dumps(shifted, ensure_ascii=False))


def _classify_node(
    node: dict[str, Any],
    siblings: list[dict[str, Any]] | None = None,
    *,
    allow_merged_member: bool = False,
) -> tuple[bool, str | None]:
    if not allow_merged_member and _is_merged_window_node(node, siblings or []):
        return False, "merged_section"
    if not has_own_body_text(node.get("text") or ""):
        return False, "empty"
    return True, None


def read_editable_node(handle: str, *, allow_merged_member: bool = False) -> dict[str, Any]:
    doc_id, node_id = parse_handle(handle)
    doc = load_workspace_doc(doc_id)
    node, ancestors = find_node(doc, node_id)
    body = extract_body_text(node.get("text") or "")
    editable, reason = _classify_node(
        node,
        _siblings_for_node(doc, ancestors),
        allow_merged_member=allow_merged_member,
    )
    return {
        "handle": handle,
        "doc_id": doc_id,
        "node_id": node_id,
        "title": node.get("title", ""),
        "path": " > ".join(
            [
                doc.get("doc_name", ""),
                *(a.get("title", "") for a in ancestors),
                node.get("title", ""),
            ]
        ).strip(" >"),
        "text": body,
        "text_hash": text_hash(body),
        "has_children": bool(node.get("nodes")),
        "has_text": has_own_body_text(node.get("text") or ""),
        "editable": editable,
        "editable_reason": reason,
        "summary_status": node.get("summary_status", "clean"),
    }


def publish_workspace_edit_sync(
    doc_id: str,
    node_id: str,
    base_hash: str,
    after_text: str,
    *,
    rebuild: Callable[[], dict[str, Any]] | None = None,
    active_reparse_check: Callable[[], bool] | None = None,
    allow_merged_member: bool = False,
) -> PublishResult:
    rebuild = rebuild or rebuild_from_workspace
    with INDEX_LOCK:
        if active_reparse_check is not None and active_reparse_check():
            raise DoceditConflict("文档正在重新解析，暂不能编辑")
        doc = load_workspace_doc(doc_id)
        md_path = Path(doc["path"])
        markdown = md_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        lines = markdown.split("\n")
        if int(doc.get("line_count") or 0) != len(lines):
            raise DoceditConflict("workspace line_count mismatch")
        node, ancestors = find_node(doc, node_id)
        editable, reason = _classify_node(
            node,
            _siblings_for_node(doc, ancestors),
            allow_merged_member=allow_merged_member,
        )
        if not editable:
            raise DoceditConflict(reason or "node is not editable")
        before_body = extract_body_text(node.get("text") or "")
        if text_hash(before_body) != base_hash:
            raise DoceditConflict("node changed before publish")
        all_nodes = _nodes_by_line(doc)
        idx = all_nodes.index(node)
        next_node = all_nodes[idx + 1] if idx + 1 < len(all_nodes) else None
        start_idx = int(node["line_num"]) - 1
        end_idx = int(next_node["line_num"]) - 1 if next_node else len(lines)
        if (
            start_idx < 0
            or start_idx >= len(lines)
            or not re.match(r"#{1,6}\s", lines[start_idx].strip())
        ):
            raise DoceditConflict("node heading boundary mismatch")
        old_lines = lines[start_idx:end_idx]
        old_block = "\n".join(old_lines).strip()
        if _normalize_block(old_block) != _normalize_block(node.get("text") or ""):
            raise DoceditConflict("markdown/workspace node mismatch")
        if _body_has_heading(old_lines):
            raise DoceditConflict("node body contains nested heading")
        if any(re.match(r"#{1,6}\s", ln.strip()) for ln in after_text.split("\n")):
            raise DoceditBadRequest("after_text must not contain headings")
        heading = lines[start_idx]
        body_lines = after_text.strip("\n").split("\n") if after_text.strip("\n") else [""]
        new_block = [heading, *body_lines]
        new_lines = [*lines[:start_idx], *new_block, *lines[end_idx:]]
        line_delta = len(new_block) - len(old_lines)
        node["text"] = "\n".join(new_block).strip()
        doc["line_count"] = len(new_lines)
        for item in all_nodes:
            if int(item.get("line_num") or 0) > int(node["line_num"]):
                item["line_num"] = int(item["line_num"]) + line_delta
        snapshot = snapshot_live_artifacts(doc)
        try:
            write_text_atomic(md_path, "\n".join(new_lines))
            write_json_atomic(
                get_settings().data_dir / "workspace" / f"{safe_name(doc_id)}.json",
                doc,
                indent=2,
            )
            _shift_pagemap(md_path, end_idx + 1, line_delta)
            try:
                index_result = rebuild()
            except Exception as exc:
                raise DoceditServiceUnavailable("索引重建失败，已恢复原文档") from exc
            return PublishResult(
                before_text=before_body,
                after_text=after_text,
                text_hash=text_hash(after_text),
                line_delta=line_delta,
                index_result=index_result,
                snapshot=snapshot,
            )
        except BaseException:
            restore_live_artifacts(snapshot)
            raise


class WorkspaceAdapter:
    def read_editable_node(
        self,
        handle: str,
        *,
        allow_merged_member: bool = False,
    ) -> dict[str, Any]:
        return read_editable_node(handle, allow_merged_member=allow_merged_member)

    def publish(
        self,
        doc_id: str,
        node_id: str,
        base_hash: str,
        after_text: str,
        active_reparse_check: Callable[[], bool] | None = None,
        allow_merged_member: bool = False,
    ) -> PublishResult:
        return publish_workspace_edit_sync(
            doc_id,
            node_id,
            base_hash,
            after_text,
            active_reparse_check=active_reparse_check,
            allow_merged_member=allow_merged_member,
        )

"""Capture explicit local corpus inputs under Windows read-only sharing leases.

All source handles remain open with write/delete sharing denied until the last
buffer is read. This is not a copy-and-rehash claim about a concurrently written
directory. An active writer causes capture to fail; no process is stopped.
Indexes are intentionally excluded: the evaluation release rebuilds its index.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from evals.agent_artifacts import MAX_INPUT_BYTES, strict_json, write_json


def _controlled(root: Path, path: Path) -> Path:
    # Check lexical components before resolve; resolving first hides junctions.
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(root):
        raise ValueError("source reference is outside approved data root")
    for part in (absolute, *absolute.parents):
        if part == root.parent:
            break
        stat = part.lstat()
        if part.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise ValueError("source reparse point is not allowed")
    resolved = absolute.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("source reference is not a regular approved file")
    return resolved


@contextmanager
def windows_read_handle(path: Path):
    if os.name != "nt":
        raise ValueError("exclusive source capture requires Windows sharing leases")
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError("source is unavailable for a read-only sharing lease")
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    try:
        final_name = kernel.GetFinalPathNameByHandleW
        final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final_name.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = final_name(handle, buffer, len(buffer), 0)
        final = buffer.value.removeprefix("\\\\?\\")
        if (not size or size >= len(buffer)
                or os.path.normcase(final) != os.path.normcase(str(path))):
            raise ValueError("leased source identity differs from approved path")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def _read(stream: BinaryIO) -> bytes:
    stream.seek(0)
    data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("source file exceeds input limit")
    return data


def capture_source(source: Path, output: Path) -> dict:
    source = Path(os.path.abspath(source))
    if source.is_symlink() or getattr(source.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("source root cannot be a reparse point")
    source = source.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(source):
        raise ValueError("capture output must be new and outside source")
    buffers: dict[str, bytes] = {}
    readers: dict[str, BinaryIO] = {}
    with ExitStack() as leases:
        def acquire(path: Path) -> None:
            path = _controlled(source, path)
            relative = path.relative_to(source).as_posix()
            if relative not in readers:
                readers[relative] = leases.enter_context(windows_read_handle(path))

        # Catalog first: it cannot publish a different document membership while
        # the selected document and Markdown handles are being acquired.
        catalog_path = source / "catalog" / "document_catalog.json"
        acquire(catalog_path)
        catalog = strict_json(_read(readers["catalog/document_catalog.json"]).decode("utf-8"))
        if not isinstance(catalog, list) or not catalog:
            raise ValueError("empty or invalid catalog")
        initial_workspace = sorted((source / "workspace").glob("*.json"))
        for path in initial_workspace:
            acquire(path)
        dictionary = source / "domain_dict_auto.txt"
        if dictionary.exists():
            acquire(dictionary)
        identities = [item.get("doc_id") or item.get("id") for item in catalog
                      if isinstance(item, dict)]
        if len(identities) != len(catalog) or any(
            not isinstance(identity, str) or not identity.startswith("doc_")
            for identity in identities
        ):
            raise ValueError("catalog identities are invalid")
        ids = set(identities)
        if len(ids) != len(catalog):
            raise ValueError("catalog identities are invalid")
        for relative, stream in list(readers.items()):
            if relative.startswith("workspace/doc_"):
                doc = strict_json(_read(stream).decode("utf-8"))
                if (not isinstance(doc, dict) or doc.get("id") not in ids
                        or Path(relative).stem != doc["id"]):
                    raise ValueError("workspace and catalog membership differ")
                md = Path(doc.get("path") or "")
                if not md.is_absolute():
                    # Workspace paths are project-relative only when explicitly
                    # based at the original backend/data parent.
                    md = source.parent / md
                acquire(md)
        if {path.stem for path in initial_workspace if path.name.startswith("doc_")} != ids:
            raise ValueError("catalog lacks a selected workspace document")
        if sorted((source / "workspace").glob("*.json")) != initial_workspace:
            raise ValueError("workspace membership changed while acquiring leases")
        # No input buffer is accepted until every required file is leased.
        for relative, stream in readers.items():
            buffers[relative] = _read(stream)
        # Publish while the complete selected source view is still leased.
        output.mkdir(parents=True, exist_ok=False)
        for relative, data in buffers.items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = {
            "source_capture_schema_version": 1,
            "capture_method": "windows_read_handles_deny_write_delete",
            "all_handles_held_during_read": True,
            "source_data_dir": str(source),
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "indexes": "excluded_rebuild_required",
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in buffers.items()},
        }
        write_json(output / "source-capture.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = capture_source(args.source_data_dir, args.output)
    except (OSError, ValueError, UnicodeError):
        print("source capture failed validation or could not obtain all leases", file=sys.stderr)
        return 2
    print(json.dumps({"captured_files": len(receipt["files"]), "indexes": "rebuild_required"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

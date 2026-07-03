from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import get_settings
from app.core.retrieval import TreeStore

_store: TreeStore | None = None
_store_sig: tuple[float | None, float | None] | None = None
_store_lock = threading.Lock()


def _catalog_sig() -> tuple[float | None, float | None]:
    data_dir = Path(get_settings().data_dir)
    catalog = data_dir / "catalog" / "document_catalog.json"
    vec_meta = data_dir / "indexes" / "vec_meta.json"
    return (
        catalog.stat().st_mtime if catalog.exists() else None,
        vec_meta.stat().st_mtime if vec_meta.exists() else None,
    )


def get_tree_store() -> TreeStore:
    global _store, _store_sig
    sig = _catalog_sig()
    if _store is not None and sig == _store_sig:
        return _store
    with _store_lock:
        if _store is None or sig != _store_sig:
            if _store is None:
                _store = TreeStore(get_settings().data_dir)
            else:
                _store = _store.incremental_clone()
            _store_sig = sig
    return _store


def set_tree_store_for_tests(store: TreeStore) -> None:
    global _store, _store_sig
    with _store_lock:
        _store = store
        _store_sig = _catalog_sig()


def clear_tree_store_for_tests() -> None:
    global _store, _store_sig
    with _store_lock:
        _store = None
        _store_sig = None

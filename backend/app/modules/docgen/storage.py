from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from app.core.config import get_settings
from app.core.fsutil import safe_name, write_json_atomic
from app.modules.docgen.schemas import DocgenSessionRecord

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _session_dir() -> Path:
    return get_settings().data_dir / "docgen" / "sessions"


def _safe_session_id(session_id: str) -> str:
    safe = safe_name(session_id, maxlen=96)
    if safe != session_id:
        raise ValueError("invalid session_id")
    return safe


def session_path(session_id: str) -> Path:
    return _session_dir() / f"{_safe_session_id(session_id)}.json"


def _lock_for(session_id: str) -> threading.Lock:
    safe = _safe_session_id(session_id)
    with _locks_guard:
        if safe not in _locks:
            _locks[safe] = threading.Lock()
        return _locks[safe]


def _load_session_unlocked(session_id: str) -> DocgenSessionRecord | None:
    path = session_path(session_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocgenSessionRecord.model_validate(data)


def _save_session_unlocked(record: DocgenSessionRecord) -> None:
    write_json_atomic(
        session_path(record.session_id),
        record.model_dump(mode="json"),
        indent=2,
    )


def load_session(session_id: str) -> DocgenSessionRecord | None:
    with _lock_for(session_id):
        return _load_session_unlocked(session_id)


def save_session(record: DocgenSessionRecord) -> None:
    with _lock_for(record.session_id):
        _save_session_unlocked(record)


def update_session(
    session_id: str,
    mutator: Callable[[DocgenSessionRecord], DocgenSessionRecord],
) -> DocgenSessionRecord | None:
    with _lock_for(session_id):
        record = _load_session_unlocked(session_id)
        if record is None:
            return None
        updated = mutator(record)
        _save_session_unlocked(updated)
        return updated

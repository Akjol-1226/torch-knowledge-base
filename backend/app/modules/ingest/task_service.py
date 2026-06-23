"""入库任务异步队列（文件存储，与 data/ 其余产物一致；v0 单机内部工具）。

每个任务一个 JSON：data/tasks/<task_id>.json
  {id, filename, kb, status, progress, error, attempts, created_at, updated_at, ...}

状态机（PRD 附录 A.1.3）：
  queued(排队中) → processing(处理中) → needs_review(待审) / done(已入库)
  任意阶段异常 → failed(失败)，自动重试 MAX_RETRIES 次后才置 failed。
列表按 created_at 倒序。task_id 由后端生成。
"""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("ingest.task")

# 状态常量
QUEUED = "queued"
PROCESSING = "processing"
NEEDS_REVIEW = "needs_review"
DONE = "done"
FAILED = "failed"

# 失败自动重试次数 + 退避（秒）；PRD 附录 A.1.3：30s / 2min / 10min
MAX_RETRIES = 3
RETRY_BACKOFF = [30, 120, 600]

# 后台并发数（PRD §3.4 可配）；env INGEST_CONCURRENCY 覆盖，默认 3
DEFAULT_CONCURRENCY = int(os.environ.get("INGEST_CONCURRENCY", "3"))


def _dir() -> Path:
    return get_settings().data_dir / "tasks"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path(task_id: str) -> Path:
    safe = "".join(c for c in (task_id or "") if c.isalnum() or c in "-_")[:64] or "task"
    return _dir() / f"{safe}.json"


def create(filename: str, kb: str) -> dict:
    """新建任务，初始 queued。"""
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    task_id = "t-" + uuid.uuid4().hex[:12]
    rec = {
        "id": task_id,
        "filename": filename,
        "kb": kb,
        "status": QUEUED,
        "progress": 0,
        "error": None,
        "attempts": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _path(task_id).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("task_created", task_id=task_id, filename=filename, kb=kb)
    return rec


def get(task_id: str) -> dict | None:
    f = _path(task_id)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def list_tasks() -> list[dict]:
    """全部任务，按 created_at 倒序。"""
    d = _dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            log.warning("task_skip_bad_file", file=f.name)
            continue
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def update(task_id: str, **fields) -> dict | None:
    """更新任务字段（自动刷新 updated_at）。"""
    f = _path(task_id)
    if not f.exists():
        return None
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec.update(fields)
    rec["updated_at"] = _now()
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def delete(task_id: str) -> bool:
    f = _path(task_id)
    if f.exists():
        f.unlink()
        return True
    return False

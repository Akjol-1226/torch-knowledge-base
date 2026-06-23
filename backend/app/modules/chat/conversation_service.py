"""会话持久化（文件存储，与 data/ 其余产物一致；v0 无登录、单机内部工具）。

每会话一个 JSON：data/conversations/<id>.json
  {id, title, created_at, updated_at, messages:[{role, content, ts}]}
列表按 updated_at 倒序。id 由前端生成（uuid），存盘前做防穿越清洗。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("chat.conversation")


def _dir() -> Path:
    return get_settings().data_dir / "conversations"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_id(cid: str) -> str:
    """防目录穿越：只保留字母数字和 -_，限长。"""
    keep = "".join(c for c in (cid or "") if c.isalnum() or c in "-_")
    return keep[:64] or "conv"


def list_conversations() -> list[dict]:
    """会话列表（id/title/updated_at/message_count），按更新时间倒序。"""
    d = _dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            log.warning("conv_list_skip_bad_file", file=f.name)
            continue
        out.append(
            {
                "id": r.get("id"),
                "title": r.get("title") or "新对话",
                "updated_at": r.get("updated_at"),
                "message_count": len(r.get("messages", [])),
            }
        )
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def get_conversation(cid: str) -> dict | None:
    """取单会话全部内容（含 messages）。"""
    f = _dir() / f"{_safe_id(cid)}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def append_turn(
    cid: str, user_msg: str, assistant_msg: str, title_hint: str | None = None
) -> dict:
    """追加一轮对话（user + assistant）；会话不存在则新建，标题取首条消息前 40 字。"""
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{_safe_id(cid)}.json"
    if f.exists():
        rec = json.loads(f.read_text(encoding="utf-8"))
    else:
        rec = {
            "id": cid,
            "title": (title_hint or user_msg or "新对话").strip()[:40] or "新对话",
            "created_at": _now(),
            "messages": [],
        }
    rec["messages"].append({"role": "user", "content": user_msg, "ts": _now()})
    rec["messages"].append({"role": "assistant", "content": assistant_msg, "ts": _now()})
    rec["updated_at"] = _now()
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("conv_append", cid=rec["id"], turns=len(rec["messages"]) // 2)
    return rec


def delete_conversation(cid: str) -> bool:
    f = _dir() / f"{_safe_id(cid)}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def rename_conversation(cid: str, title: str) -> bool:
    f = _dir() / f"{_safe_id(cid)}.json"
    if not f.exists():
        return False
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["title"] = (title or "").strip()[:80] or rec.get("title") or "新对话"
    rec["updated_at"] = _now()
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return True

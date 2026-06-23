"""notsure 人工审核闸门：待审存储 + 审核 + 通过后写回 md 建树。

入库流程的质量闸门：PDF 解析出的 md 若含 <notsure>（VLM 不确定项），先落"待审区"
（data/pending/ 存 md，data/review/ 存条目+状态），不进知识库；管理员逐条确认/修正后
approve —— 用确认值替换 notsure 段写回 data/md/，再建树正式入库。
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.ingest.notsure_service import extract_notsure

log = get_logger("ingest.review")
_NOTSURE = re.compile(r"<notsure>(.*?)</notsure>", re.S)


def _dirs() -> tuple[Path, Path, Path]:
    d = get_settings().data_dir
    return d / "pending", d / "review", d / "md"


def save_pending(
    stem: str, md_text: str, original_name: str | None = None, kb: str = "default"
) -> dict:
    """把含 notsure 的待审文档存入待审区（记 kb），返回审核记录。"""
    pending, review, _ = _dirs()
    pending.mkdir(parents=True, exist_ok=True)
    review.mkdir(parents=True, exist_ok=True)
    (pending / f"{stem}.md").write_text(md_text, encoding="utf-8")
    rec = {
        "doc": stem,
        "kb": kb,
        "original_name": original_name or stem,
        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "needs_review",
        "notsure": extract_notsure(md_text),
    }
    (review / f"{stem}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("review_pending_saved", doc=stem, notsure=len(rec["notsure"]))
    return rec


def list_pending() -> list[dict]:
    """列出所有待审文档（卡片用：文档名/上传时间/待确认处数）。"""
    _, review, _ = _dirs()
    if not review.exists():
        return []
    out: list[dict] = []
    for f in sorted(review.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        out.append(
            {
                "doc": r["doc"],
                "kb": r.get("kb", "default"),
                "original_name": r.get("original_name"),
                "uploaded_at": r.get("uploaded_at"),
                "notsure_count": len(r.get("notsure", [])),
                "status": r.get("status"),
            }
        )
    return out


def get_review(doc: str) -> dict | None:
    """取某待审文档的全部 notsure 条目（审核详情页用）。"""
    _, review, _ = _dirs()
    f = review / f"{doc}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def approve(doc: str, resolutions: dict[str, str] | None = None, *, build: bool = True) -> dict:
    """审核通过：用 resolutions（按 notsure 序号 → 确认/修正值）替换所有 notsure 段，
    写回 data/md/，移出待审区，建树正式入库。

    未提供某序号的 resolution 时，默认采用 VLM 原识别内容（等价"标记正确"）。
    """
    pending, review, md_dir = _dirs()
    pf = pending / f"{doc}.md"
    rf = review / f"{doc}.json"
    if not pf.exists():
        return {"error": f"待审文档不存在: {doc}"}
    resolutions = resolutions or {}
    kb = "default"
    if rf.exists():
        kb = json.loads(rf.read_text(encoding="utf-8")).get("kb", "default")
    md_text = pf.read_text(encoding="utf-8")

    counter = [0]

    def _repl(m: re.Match) -> str:
        counter[0] += 1
        v = resolutions.get(str(counter[0]))
        return v if v is not None else m.group(1)  # 默认取原识别内容、去标记

    resolved = _NOTSURE.sub(_repl, md_text)

    target = md_dir / kb  # 审核通过后按 kb 落子目录
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{doc}.md").write_text(resolved, encoding="utf-8")
    pf.unlink()
    rf.unlink(missing_ok=True)
    log.info("review_approved", doc=doc, kb=kb, resolved=counter[0])

    result: dict = {"doc": doc, "kb": kb, "status": "approved", "resolved": counter[0]}
    if build:
        from app.modules.ingest.tree_service import ingest_default

        result["tree"] = ingest_default()
    return result

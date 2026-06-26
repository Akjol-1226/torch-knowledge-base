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
from app.core.fsutil import safe_name, write_json_atomic, write_text_atomic
from app.core.logging import get_logger
from app.modules.ingest.locks import INDEX_LOCK
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
    stem = safe_name(stem)  # 防穿越：stem 来自上传文件名
    kb = safe_name(kb, default="default")
    write_text_atomic(pending / f"{stem}.md", md_text)
    rec = {
        "doc": stem,
        "kb": kb,
        "original_name": original_name or stem,
        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "needs_review",
        "notsure": extract_notsure(md_text),
    }
    write_json_atomic(review / f"{stem}.json", rec, indent=2)
    log.info("review_pending_saved", doc=stem, notsure=len(rec["notsure"]))
    return rec


def list_pending() -> list[dict]:
    """列出所有待审文档（卡片用：文档名/上传时间/待确认处数）。"""
    _, review, _ = _dirs()
    if not review.exists():
        return []
    out: list[dict] = []
    for f in sorted(review.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("review_json_unreadable_skipped", path=str(f))
            continue
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
    f = review / f"{safe_name(doc)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("review_json_unreadable", doc=doc)
        return None


def approve(doc: str, resolutions: dict[str, str] | None = None, *, build: bool = True) -> dict:
    """审核通过：用 resolutions（按 notsure 序号 → 确认/修正值）替换所有 notsure 段，
    写回 data/md/，移出待审区，建树正式入库。

    未提供某序号的 resolution 时，默认采用 VLM 原识别内容（等价"标记正确"）。
    """
    doc = safe_name(doc)  # 防穿越：doc 来自 URL 路径参数
    pending, review, md_dir = _dirs()
    pf = pending / f"{doc}.md"
    rf = review / f"{doc}.json"
    resolutions = resolutions or {}
    # 全程持锁：串行化重复 approve（幂等）+ 让"写 md + 建树"原子，避免并发双审与丢失
    with INDEX_LOCK:
        if not pf.exists():
            # 锁内复查：可能已被另一并发请求审核通过（pf 已删）→ 幂等返回，不报 500
            return {"error": f"待审文档不存在或已审核: {doc}"}
        kb = "default"
        if rf.exists():
            try:
                kb = json.loads(rf.read_text(encoding="utf-8")).get("kb", "default")
            except (json.JSONDecodeError, OSError):
                log.warning("review_json_unreadable", doc=doc)
        kb = safe_name(kb, default="default")
        md_text = pf.read_text(encoding="utf-8")

        counter = [0]

        def _repl(m: re.Match) -> str:
            counter[0] += 1
            v = resolutions.get(str(counter[0]))
            return v if v is not None else m.group(1)  # 默认取原识别内容、去标记

        resolved = _NOTSURE.sub(_repl, md_text)

        target = md_dir / kb  # 审核通过后按 kb 落子目录
        target.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target / f"{doc}.md", resolved)
        pf.unlink(missing_ok=True)
        rf.unlink(missing_ok=True)
        log.info("review_approved", doc=doc, kb=kb, resolved=counter[0])

        result: dict = {"doc": doc, "kb": kb, "status": "approved", "resolved": counter[0]}
        if build:
            from app.modules.ingest.tree_service import ingest_one

            md_file = target / f"{doc}.md"
            # 补 OCR 侧车（与直接入库路径一致；失败不阻断）：原 PDF 在进待审前已存到
            # data/pdf/<kb>/<stem>.pdf，让经审文档同样能在「原文 PDF」里高亮被引处。
            try:
                from app.modules.ingest.ocr_locate import write_ocr_sidecar

                pdf_file = get_settings().data_dir / "pdf" / kb / f"{doc}.pdf"
                if pdf_file.exists():
                    write_ocr_sidecar(pdf_file, md_file)
            except Exception:
                log.exception("ocr_sidecar_failed_on_approve", doc=doc)
            result["tree"] = ingest_one(md_file)  # 增量入库，不重建全库
        return result

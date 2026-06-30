"""Dedicated background worker for document reparse tasks."""

import asyncio
import hashlib
import os
import time
import uuid
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.fsutil import safe_name
from app.core.logging import get_logger
from app.modules.ingest import docparse_service, task_service, tree_service
from app.modules.ingest.notsure_service import count_notsure
from app.modules.ingest.review_service import save_pending

log = get_logger("ingest.reparse")

_semaphore = asyncio.Semaphore(task_service.DEFAULT_CONCURRENCY)


def _reparse_review_doc(stem: str, doc_id: str) -> str:
    suffix = "__reparse__" + hashlib.sha1(
        doc_id.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    prefix = safe_name(stem, maxlen=64 - len(suffix))
    return f"{prefix}{suffix}"


def _cleanup_tmp_md(tmp_md: Path) -> None:
    tmp_md.unlink(missing_ok=True)
    Path(str(tmp_md) + ".pagemap.json").unlink(missing_ok=True)
    Path(str(tmp_md) + ".ocr.json").unlink(missing_ok=True)


def _write_tmp_ocr(pdf_path: str, tmp_md: Path) -> Path | None:
    try:
        from app.modules.ingest.ocr_locate import write_ocr_sidecar

        write_ocr_sidecar(pdf_path, tmp_md)
        return Path(str(tmp_md) + ".ocr.json")
    except Exception:
        log.exception("reparse_ocr_sidecar_failed", md=str(tmp_md))
        return None


def _reparse_pdf(pdf_path: str, doc_id: str, original_name: str | None, kb: str) -> dict:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    kb = safe_name(kb, default="default")
    stem = safe_name(Path(original_name or pdf_path).stem)
    tmp_md = settings.data_dir / f"_tmp_reparse_{stem}_{uuid.uuid4().hex[:8]}.md"
    tmp_pagemap = Path(str(tmp_md) + ".pagemap.json")

    try:
        start = time.perf_counter()
        docparse_service.pdf_to_markdown(pdf_path, tmp_md, title=stem)
        t_parse = time.perf_counter() - start
        md_text = tmp_md.read_text(encoding="utf-8")
        n = count_notsure(md_text)
        if n > 0:
            review_doc = _reparse_review_doc(stem, doc_id)
            rec = save_pending(
                stem,
                md_text,
                original_name,
                kb,
                kind="reparse",
                doc_id=doc_id,
                review_doc=review_doc,
            )
            return {
                "document": stem,
                "kb": kb,
                "status": "needs_review",
                "notsure_count": n,
                "notsure": rec["notsure"],
            }

        final_md = settings.data_dir / "md" / kb / f"{stem}.md"
        start = time.perf_counter()
        doc = tree_service.build_reparse_candidate(
            tmp_md,
            doc_id=doc_id,
            kb=kb,
            final_md_path=final_md,
        )
        t_tree = time.perf_counter() - start
        tmp_ocr = _write_tmp_ocr(pdf_path, tmp_md)
        tree = tree_service.commit_reparse_candidate(
            doc,
            md_text,
            tmp_pagemap=tmp_pagemap,
            tmp_ocr=tmp_ocr,
        )
        total = t_parse + t_tree
        return {
            "document": stem,
            "kb": kb,
            "status": "ready",
            "notsure_count": 0,
            "timing": {
                "parse": round(t_parse, 1),
                "tree": round(t_tree, 1),
                "total": round(total, 1),
            },
            "tree": tree,
        }
    finally:
        _cleanup_tmp_md(tmp_md)


async def run_reparse_task(
    task_id: str,
    tmp_path: str,
    doc_id: str,
    original_name: str | None,
    kb: str,
) -> None:
    """Run a reparse task without touching live data until commit."""
    try:
        for attempt in range(1, task_service.MAX_RETRIES + 1):
            async with _semaphore:
                task_service.update(
                    task_id, status=task_service.PROCESSING, progress=10, attempts=attempt
                )
                try:
                    result = await run_in_threadpool(
                        _reparse_pdf, tmp_path, doc_id, original_name, kb
                    )
                except Exception as e:  # noqa: BLE001 - task boundary records failure
                    err = f"{type(e).__name__}: {e}"
                    log.exception("reparse_task_attempt_failed", task_id=task_id, attempt=attempt)
                    if attempt >= task_service.MAX_RETRIES:
                        task_service.update(
                            task_id, status=task_service.FAILED, progress=0, error=err
                        )
                        return
                    task_service.update(
                        task_id,
                        status=task_service.QUEUED,
                        error=f"retry {attempt}/{task_service.MAX_RETRIES}: {err}",
                    )
                else:
                    final = (
                        task_service.NEEDS_REVIEW
                        if result.get("status") == "needs_review"
                        else task_service.DONE
                    )
                    task_service.update(
                        task_id,
                        status=final,
                        progress=100,
                        error=None,
                        document=result.get("document"),
                        notsure_count=result.get("notsure_count", 0),
                    )
                    log.info("reparse_task_finished", task_id=task_id, status=final)
                    return
            await asyncio.sleep(task_service.RETRY_BACKOFF[attempt - 1])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

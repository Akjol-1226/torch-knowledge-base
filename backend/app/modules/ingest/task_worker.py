"""异步入库 worker：由 upload-pdf 经 FastAPI BackgroundTasks 调度。

受全局信号量限制并发（PRD §3.4，默认 3，不上 Celery）；解析/建树是阻塞 VLM/LLM
调用，丢线程池跑，不堵事件循环。失败按 RETRY_BACKOFF 自动重试，耗尽后置 failed。
"""

import asyncio
import os

from fastapi.concurrency import run_in_threadpool

from app.core.logging import get_logger
from app.modules.ingest import task_service
from app.modules.ingest.doc_convert import needs_conversion, to_pdf
from app.modules.ingest.docparse_service import ingest_pdf

log = get_logger("ingest.worker")

# 全局并发闸门：同时在跑的解析任务数 ≤ DEFAULT_CONCURRENCY
_semaphore = asyncio.Semaphore(task_service.DEFAULT_CONCURRENCY)


def _convert_and_ingest(tmp_path: str, original_name: str | None, kb: str) -> dict:
    """非 PDF 先转 PDF 再入库；清理转换产生的临时 PDF。同步阻塞，丢线程池跑。

    转换+入库一起被 worker 的重试循环覆盖，故 Gotenberg 临时不可用也能重试。
    """
    pdf_path = tmp_path
    converted = None
    if needs_conversion(original_name or tmp_path):
        converted = to_pdf(tmp_path, original_name)
        pdf_path = str(converted)
    try:
        return ingest_pdf(pdf_path, original_name, kb)
    finally:
        if converted is not None:
            converted.unlink(missing_ok=True)


async def run_ingest_task(task_id: str, tmp_path: str, original_name: str | None, kb: str) -> None:
    """后台执行一个入库任务：processing → needs_review/done，失败重试后 → failed。"""
    try:
        for attempt in range(1, task_service.MAX_RETRIES + 1):
            # 信号量只圈住"真正在跑"的解析；退避 sleep 放在锁外，避免重试期间空占并发槽堵队列
            async with _semaphore:
                task_service.update(
                    task_id, status=task_service.PROCESSING, progress=10, attempts=attempt
                )
                try:
                    result = await run_in_threadpool(
                        _convert_and_ingest, tmp_path, original_name, kb
                    )
                except Exception as e:  # noqa: BLE001 - 任务级兜底，错误进 task.error
                    err = f"{type(e).__name__}: {e}"
                    log.exception("task_attempt_failed", task_id=task_id, attempt=attempt)
                    if attempt >= task_service.MAX_RETRIES:
                        task_service.update(
                            task_id, status=task_service.FAILED, progress=0, error=err
                        )
                        return
                    task_service.update(
                        task_id,
                        status=task_service.QUEUED,
                        error=f"重试 {attempt}/{task_service.MAX_RETRIES}：{err}",
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
                    log.info("task_finished", task_id=task_id, status=final)
                    return
            # 槽已释放，再退避等待（不占用并发额度）
            await asyncio.sleep(task_service.RETRY_BACKOFF[attempt - 1])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

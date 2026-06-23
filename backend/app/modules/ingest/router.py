import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.modules.ingest import document_service, review_service, task_service
from app.modules.ingest.schemas import UploadResponse
from app.modules.ingest.service import IngestService
from app.modules.ingest.task_worker import run_ingest_task
from app.modules.ingest.tree_service import ingest_default

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get("/stats")
def stats() -> dict:
    """知识库统计：已入库文档数 + 待审数 + 文档清单（kb-list 页用）。"""
    cat = get_settings().data_dir / "catalog" / "document_catalog.json"
    docs = json.loads(cat.read_text(encoding="utf-8")) if cat.exists() else []
    by_kb: dict[str, int] = {}
    for d in docs:
        k = d.get("kb", "default")
        by_kb[k] = by_kb.get(k, 0) + 1
    return {
        "documents": len(docs),
        "pending": len(review_service.list_pending()),
        "kbs": by_kb,
        "doc_list": docs,
    }


@router.get("/review")
def list_review() -> list[dict]:
    """列出待人工审核的文档（含 notsure，尚未入库）。"""
    return review_service.list_pending()


@router.get("/review/{doc}")
def get_review(doc: str) -> dict:
    """取某待审文档的全部 notsure 条目（审核详情页用）。"""
    r = review_service.get_review(doc)
    if r is None:
        raise HTTPException(status_code=404, detail=f"待审文档不存在: {doc}")
    return r


class ApproveRequest(BaseModel):
    # 按 notsure 序号（"1"/"2"…）→ 确认或修正后的值；缺省的序号默认采用 VLM 原识别内容
    resolutions: dict[str, str] = {}


@router.post("/review/{doc}/approve")
async def approve_review(doc: str, req: ApproveRequest) -> dict:
    """审核通过：用 resolutions 替换 notsure 段→写回 data/md/→建树入库（建树丢线程池）。"""
    return await run_in_threadpool(review_service.approve, doc, req.resolutions)


@router.post("/build-tree")
def build_tree() -> dict:
    """切片 1：扫 data/md/*.md 建 PageIndex 树 + BM25 索引（落 data/ 文件存储）。

    同步 def：build_tree 内部用 asyncio.run，FastAPI 会把同步路由丢线程池执行，
    避免在运行中的事件循环里调 asyncio.run 报错。需先配 LiteLLM Proxy（.env）。
    """
    return ingest_default()


@router.post("/upload-pdf")
async def upload_pdf(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    kb: str = Form("default"),
) -> dict:
    """PDF 直传 → 入异步队列：建任务(queued) + 后台解析建树，立即返回 task_id。

    后台 worker 受并发信号量限制；解析/建树是阻塞 VLM/LLM 调用，丢线程池跑。
    临时文件由 worker 跑完后清理（不能用 with 自动删，否则后台还没读就没了）。
    """
    content = await file.read()
    suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    task = task_service.create(file.filename or "upload.pdf", kb)
    background.add_task(run_ingest_task, task["id"], tmp_path, file.filename, kb)
    return {"task_id": task["id"], "status": task["status"]}


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
) -> UploadResponse:
    content = await file.read()
    service = IngestService(session)
    return await service.upload_document(
        filename=file.filename or "unknown", content=content
    )


@router.get("/tasks")
async def list_tasks() -> list[dict]:
    """入库任务列表（文件存储，按提交时间倒序）。"""
    return await run_in_threadpool(task_service.list_tasks)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    """单个任务状态（前端轮询用）。"""
    rec = await run_in_threadpool(task_service.get, task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return rec


@router.get("/document/{doc_id}")
async def view_document(doc_id: str) -> dict:
    """查看文档：解析后的 Markdown 全文 + 是否有原 PDF。"""
    rec = await run_in_threadpool(document_service.get_document, doc_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return rec


@router.get("/document/{doc_id}/pdf")
async def download_document_pdf(doc_id: str) -> FileResponse:
    """元文档：原 PDF 文件流（历史从 md 入库的文档无 PDF → 404）。"""
    p = await run_in_threadpool(document_service.get_pdf_file, doc_id)
    if p is None:
        raise HTTPException(status_code=404, detail="该文档无原 PDF（仅有解析后的 Markdown）")
    return FileResponse(str(p), media_type="application/pdf")


@router.delete("/document/{doc_id}")
async def remove_document(doc_id: str) -> dict:
    """删除文档：删 md + 原 PDF → 重建树/索引/目录。"""
    return await run_in_threadpool(document_service.delete_document, doc_id)

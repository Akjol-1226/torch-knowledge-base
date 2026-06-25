"""多格式 → PDF 转换（经 Gotenberg = 封装 LibreOffice 的无状态转换服务）。

非 PDF 上传（docx/xlsx/pptx/txt 等）先转成 PDF，再走现有 PDF 入库管线（VLM/建树/索引）。
PDF 原样透传。Gotenberg 跑在独立容器里，进程隔离 + 并发/超时由它内部兜底，比在本进程
里 subprocess 调 soffice 稳得多。CJK 字体焊进自定义镜像（见 docker/gotenberg/Dockerfile）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("ingest.convert")

# 经 Gotenberg/LibreOffice 路由可转的格式（PDF 不在此列：直接透传）
CONVERTIBLE_EXTS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".rtf", ".odt", ".csv",
}
# 上传允许的全部扩展名（含 PDF）
SUPPORTED_UPLOAD_EXTS = CONVERTIBLE_EXTS | {".pdf"}


class ConversionError(RuntimeError):
    """转换失败（Gotenberg 不可用 / 非 200 / 返回非 PDF）。"""


def _ext(name: str | os.PathLike) -> str:
    return Path(name).suffix.lower()


def needs_conversion(original_name: str) -> bool:
    return _ext(original_name) in CONVERTIBLE_EXTS


def to_pdf(src_path: str | Path, original_name: str | None = None) -> Path:
    """把 src_path 转成 PDF，返回 PDF 路径。

    - PDF：原样返回 src_path（不复制）。
    - 可转格式：POST 给 Gotenberg，结果写到新临时 .pdf 返回（调用方负责清理）。
    - 不支持的扩展名 / 转换失败：抛 ConversionError。

    同步阻塞（HTTP 调用），需从同步上下文或线程池调用。
    """
    name = original_name or str(src_path)
    ext = _ext(name)
    if ext == ".pdf":
        return Path(src_path)
    if ext not in CONVERTIBLE_EXTS:
        raise ConversionError(f"不支持的文件格式：{ext or '(无扩展名)'}")

    settings = get_settings()
    url = settings.gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    try:
        with open(src_path, "rb") as f:
            # files 字段名固定为 "files"；文件名的扩展名告诉 LibreOffice 源格式
            resp = httpx.post(
                url,
                files={"files": (Path(name).name, f, "application/octet-stream")},
                timeout=settings.gotenberg_timeout,
            )
    except httpx.HTTPError as e:
        raise ConversionError(f"Gotenberg 转换服务不可用（{settings.gotenberg_url}）：{e}") from e

    if resp.status_code != 200:
        raise ConversionError(
            f"Gotenberg 转换失败 HTTP {resp.status_code}：{resp.text[:200]}"
        )
    if not resp.content.startswith(b"%PDF"):
        raise ConversionError("Gotenberg 返回的不是有效 PDF")

    fd, out = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as g:
            g.write(resp.content)
    except BaseException:
        Path(out).unlink(missing_ok=True)
        raise
    log.info("converted_to_pdf", src=Path(name).name, ext=ext, bytes=len(resp.content))
    return Path(out)

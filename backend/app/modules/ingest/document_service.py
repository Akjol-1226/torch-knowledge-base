"""文档查看 / 删除：基于 workspace/<doc_id>.json 定位解析后的 md 与原 PDF。

原 PDF 与 md 同 stem 关联（data/pdf/<kb>/<stem>.pdf）；历史从 md 直接入库的文档无原 PDF。
删除会重建树/索引/目录，chat 端 get_store 通过 catalog mtime 自动重载。
"""

import json
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("ingest.document")


def _safe(s: str) -> str:
    return "".join(c for c in (s or "") if c.isalnum() or c in "-_")[:64]


def _ws_path(doc_id: str) -> Path:
    return get_settings().data_dir / "workspace" / f"{_safe(doc_id)}.json"


def _load(doc_id: str) -> dict | None:
    f = _ws_path(doc_id)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def _pdf_path(doc: dict) -> Path:
    """原 PDF 路径：与 md 文件同 stem（不依赖 doc_name，避免解析后改名导致对不上）。"""
    kb = doc.get("kb", "default")
    stem = Path(doc["path"]).stem
    return get_settings().data_dir / "pdf" / kb / f"{stem}.pdf"


def get_document(doc_id: str) -> dict | None:
    """查看：解析后的 md 全文 + 是否有原 PDF（前端据 has_pdf 决定能否切到 PDF 视图）。"""
    doc = _load(doc_id)
    if doc is None:
        return None
    md_path = Path(doc["path"])
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    return {
        "doc_id": doc_id,
        "doc_name": doc.get("doc_name", ""),
        "kb": doc.get("kb", "default"),
        "md": md,
        "has_pdf": _pdf_path(doc).exists(),
    }


def get_md_path(doc_id: str) -> Path | None:
    """文档解析后 md 的路径（OCR/页码侧车与它同目录同名）。无则 None。"""
    doc = _load(doc_id)
    return Path(doc["path"]) if doc else None


def get_pdf_file(doc_id: str) -> Path | None:
    """原 PDF 文件路径（无则 None）。"""
    doc = _load(doc_id)
    if doc is None:
        return None
    p = _pdf_path(doc)
    return p if p.exists() else None


def delete_document(doc_id: str) -> dict:
    """删除文档：删 md + 原 PDF + workspace json，重建树/索引/目录。"""
    doc = _load(doc_id)
    if doc is None:
        return {"error": f"文档不存在: {doc_id}"}
    md_path = Path(doc["path"])
    if md_path.exists():
        md_path.unlink()
    Path(str(md_path) + ".pagemap.json").unlink(missing_ok=True)  # 一并删页码侧车，避免孤儿
    Path(str(md_path) + ".ocr.json").unlink(missing_ok=True)      # 一并删 OCR 侧车
    pdf = _pdf_path(doc)
    if pdf.exists():
        pdf.unlink()
    _ws_path(doc_id).unlink(missing_ok=True)
    log.info("document_deleted", doc_id=doc_id, doc_name=doc.get("doc_name"))

    from app.modules.ingest.tree_service import ingest_default

    tree = ingest_default()
    return {"deleted": doc_id, "doc_name": doc.get("doc_name", ""), "tree": tree}

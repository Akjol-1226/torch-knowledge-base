"""PDF → Markdown 直传（封装 core/docparse = DocVisionMD/pdf_vlm_md）。

上传的 PDF 经 VLM 逐页解析成 Markdown（含复杂表格的 HTML 还原、<notsure> 保留），
落到 data/md/，可接着调 tree_service 建树。VLM 走 LiteLLM Proxy 的 vision 模型
（见 config.apply_docparse_env）。
"""

import shutil
import time
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.docparse import convert_pdf_to_markdown
from app.core.fsutil import safe_name
from app.core.logging import get_logger

log = get_logger("ingest.docparse")


def pdf_to_markdown(
    pdf_path: str | Path, out_md_path: str | Path, title: str | None = None
) -> Path:
    """把单个 PDF 转成 Markdown，写到 out_md_path。返回 md 路径。

    title：文档大标题（H1）。上传走临时 PDF，stem 是临时名，须显式传真实文档名，
    否则 H1 会变成 tmpXXXX。同步阻塞（VLM 逐页调用），需从同步上下文或线程池调用。
    """
    settings = get_settings()
    settings.apply_docparse_env()  # 桥接 QWEN_* 必须在 docparse get_config 初始化前
    convert_pdf_to_markdown(pdf_path=str(pdf_path), output_path=str(out_md_path), title=title)
    log.info("pdf_converted", pdf=str(pdf_path), md=str(out_md_path))
    return Path(out_md_path)


def ingest_pdf(
    pdf_path: str | Path, original_name: str | None = None, kb: str = "default"
) -> dict:
    """PDF 直传，归属知识库 kb，按 notsure 自动分流：

    - 含 notsure（VLM 不确定）→ 进待审区（data/pending/ + data/review/，记 kb），**不建树**，
      等人工审核闸门通过后再入库（见 review_service）。
    - 无 notsure → 直接写 data/md/<kb>/ 并建树正式入库。
    """
    from app.modules.ingest.notsure_service import count_notsure
    from app.modules.ingest.review_service import save_pending

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # kb / stem 来自表单与上传文件名 → 收敛成安全名，防路径穿越（../ 等）写出 data/ 外
    kb = safe_name(kb, default="default")
    stem = safe_name(Path(original_name or pdf_path).stem)

    # 保留原 PDF 供查看（与 md 同 stem 关联：data/pdf/<kb>/<stem>.pdf）
    pdf_dir = settings.data_dir / "pdf" / kb
    pdf_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(pdf_path, pdf_dir / f"{stem}.pdf")

    # 先解析到临时文件，再按 notsure 决定落待审区还是直接入库
    # 临时名带 uuid：同名文件并发上传不再共用同一 _tmp 路径而互相覆盖
    tmp_md = settings.data_dir / f"_tmp_{stem}_{uuid.uuid4().hex[:8]}.md"
    # 用真实文档名做 H1（stem 来自 original_name），避免临时 PDF 名 tmpXXXX 成为文档大标题
    _t = time.perf_counter()
    pdf_to_markdown(pdf_path, tmp_md, title=stem)  # 同时写 tmp_md.pagemap.json（行→PDF页 侧车）
    t_parse = time.perf_counter() - _t  # PDF→MD 总时长（细分见 convert 的 [timing] 汇总）
    md_text = tmp_md.read_text(encoding="utf-8")
    tmp_pagemap = Path(str(tmp_md) + ".pagemap.json")

    n = count_notsure(md_text)
    if n > 0:
        # 进待审区：清理临时产物（页码侧车在审核入库后由建树侧重算，见 review_service）
        tmp_md.unlink(missing_ok=True)
        tmp_pagemap.unlink(missing_ok=True)
        rec = save_pending(stem, md_text, original_name, kb)
        return {
            "document": stem,
            "kb": kb,
            "status": "needs_review",
            "notsure_count": n,
            "notsure": rec["notsure"],
        }

    # 无 notsure：直接入库（按 kb 落子目录）
    from app.modules.ingest.tree_service import ingest_one

    md_dir = settings.data_dir / "md" / kb
    md_dir.mkdir(parents=True, exist_ok=True)
    (md_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
    # 页码侧车随 md 一起落到正式目录（建树时 annotate_pages 会读它给节点标页）
    final_md = md_dir / f"{stem}.md"
    if tmp_pagemap.exists():
        shutil.move(str(tmp_pagemap), str(md_dir / f"{stem}.md.pagemap.json"))
    tmp_md.unlink(missing_ok=True)
    # OCR 原 PDF → <md>.ocr.json（扫描件文字框，供「原文 PDF」高亮被引用处）；失败不阻断入库
    t_ocr = 0.0
    try:
        from app.modules.ingest.ocr_locate import write_ocr_sidecar

        _t = time.perf_counter()
        n_boxes = write_ocr_sidecar(pdf_dir / f"{stem}.pdf", final_md)
        t_ocr = time.perf_counter() - _t
        log.info("ocr_sidecar_written", stem=stem, boxes=n_boxes, secs=round(t_ocr, 1))
    except Exception:
        log.exception("ocr_sidecar_failed", stem=stem)

    _t = time.perf_counter()
    tree = ingest_one(final_md)  # 增量入库：只建本篇树，其余文档复用 workspace
    t_tree = time.perf_counter() - _t

    total = t_parse + t_ocr + t_tree
    log.info(
        "ingest_timing", stem=stem,
        parse=round(t_parse, 1), ocr=round(t_ocr, 1), tree=round(t_tree, 1),
        total=round(total, 1),
        parse_pct=f"{t_parse / total * 100:.0f}%" if total else "-",
        ocr_pct=f"{t_ocr / total * 100:.0f}%" if total else "-",
        tree_pct=f"{t_tree / total * 100:.0f}%" if total else "-",
    )
    return {
        "document": stem,
        "kb": kb,
        "status": "ready",
        "notsure_count": 0,
        "timing": {"parse": round(t_parse, 1), "ocr": round(t_ocr, 1),
                   "tree": round(t_tree, 1), "total": round(total, 1)},
        "tree": tree,
    }

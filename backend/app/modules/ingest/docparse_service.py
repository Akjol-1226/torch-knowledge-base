"""PDF → Markdown 直传（封装 core/docparse = DocVisionMD/pdf_vlm_md）。

上传的 PDF 经 VLM 逐页解析成 Markdown（含复杂表格的 HTML 还原、<notsure> 保留），
落到 data/md/，可接着调 tree_service 建树。VLM 走 LiteLLM Proxy 的 vision 模型
（见 config.apply_docparse_env）。
"""

import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.docparse import convert_pdf_to_markdown
from app.core.logging import get_logger

log = get_logger("ingest.docparse")


def pdf_to_markdown(pdf_path: str | Path, out_md_path: str | Path) -> Path:
    """把单个 PDF 转成 Markdown，写到 out_md_path。返回 md 路径。

    同步阻塞（VLM 逐页调用），需从同步上下文或线程池调用。
    """
    settings = get_settings()
    settings.apply_docparse_env()  # 桥接 QWEN_* 必须在 docparse get_config 初始化前
    convert_pdf_to_markdown(pdf_path=str(pdf_path), output_path=str(out_md_path))
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
    stem = Path(original_name or pdf_path).stem

    # 保留原 PDF 供查看（与 md 同 stem 关联：data/pdf/<kb>/<stem>.pdf）
    pdf_dir = settings.data_dir / "pdf" / kb
    pdf_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(pdf_path, pdf_dir / f"{stem}.pdf")

    # 先解析到临时文件，再按 notsure 决定落待审区还是直接入库
    tmp_md = settings.data_dir / f"_tmp_{stem}.md"
    pdf_to_markdown(pdf_path, tmp_md)
    md_text = tmp_md.read_text(encoding="utf-8")
    tmp_md.unlink(missing_ok=True)

    n = count_notsure(md_text)
    if n > 0:
        rec = save_pending(stem, md_text, original_name, kb)
        return {
            "document": stem,
            "kb": kb,
            "status": "needs_review",
            "notsure_count": n,
            "notsure": rec["notsure"],
        }

    # 无 notsure：直接入库（按 kb 落子目录）
    from app.modules.ingest.tree_service import ingest_default

    md_dir = settings.data_dir / "md" / kb
    md_dir.mkdir(parents=True, exist_ok=True)
    (md_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
    return {
        "document": stem,
        "kb": kb,
        "status": "ready",
        "notsure_count": 0,
        "tree": ingest_default(),
    }

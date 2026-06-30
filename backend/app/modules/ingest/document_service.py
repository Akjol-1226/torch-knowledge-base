"""文档查看 / 删除：基于 workspace/<doc_id>.json 定位解析后的 md 与原 PDF。

原 PDF 与 md 同 stem 关联（data/pdf/<kb>/<stem>.pdf）；历史从 md 直接入库的文档无原 PDF。
删除会重建树/索引/目录，chat 端 get_store 通过 catalog mtime 自动重载。
"""

import json
import re
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("ingest.document")

# 通用占位标题（附表1 / 附件2 / 表3 / 图1）不参与同名窗口合并——它们在不同段落会重复出现，
# 按标题相等合并会把不相干的表错并到一起。与 retrieval.treestore 的判定保持一致。
_GENERIC_TITLE = re.compile(r"^(附表|附件|表|图)\s*\d*$")


def _groupable_title(title: str) -> bool:
    t = (title or "").strip()
    # 归一后再判通用占位，与合并键 _norm_title 口径一致（防"附 表1"漏过判定误并）。
    return bool(t) and not _GENERIC_TITLE.match(_norm_title(title))


def _norm_title(title: str) -> str:
    """同名窗口合并用的标题归一:去掉所有空白。解析常把同一工序的多段窗口标题空格写得
    不一致(「G03 涂布工序工艺规程」vs「G03涂布工序工艺规程」),精确相等会把本该合并的章节切碎。"""
    return re.sub(r"\s+", "", title or "")


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


def get_tree(doc_id: str) -> dict | None:
    """文档的 PageIndex 树骨架（供前端「查看结构」用）：按真实章节划分的嵌套目录。

    长章节在解析时常被分页切成多个【连续同名窗口】（如 G01 配料工序重复 8 次）。这里把
    连续同名窗口合并成一个章节节点、其子节点（附表等）归并到名下，得到干净的章节目录，
    而不是把每个窗口都平铺出来。不含正文（text），保持响应轻量；id 形如 <doc_id>:<node_id>。
    """
    doc = _load(doc_id)
    if doc is None:
        return None

    def prune(nodes: list) -> list:
        out = []
        raw = list(nodes)
        i = 0
        while i < len(raw):
            n = raw[i]
            title = (n.get("title") or "").strip()
            group = [n]
            # 合并紧邻其后的同名窗口（仅限可分组标题；通用占位标题如"附表1"不合并）
            # 标题按空格归一化比较：同工序窗口标题空格不一致("G03 涂布"vs"G03涂布")也能合并
            if _groupable_title(title):
                ntitle = _norm_title(title)
                j = i + 1
                while j < len(raw) and _norm_title(raw[j].get("title")) == ntitle:
                    group.append(raw[j])
                    j += 1
                i = j
            else:
                i += 1
            first = group[0]
            merged_children: list = []
            for w in group:
                merged_children.extend(w.get("nodes", []) or [])
            page = next((w.get("page") for w in group if w.get("page") is not None), None)
            summary = next(
                (w.get("summary") for w in group if (w.get("summary") or "").strip()), ""
            )
            # 该节点自身是否有正文（去掉标题行/空行后仍有内容）。即使它有子节点，前端也据此
            # 让点出自己的正文——否则像开头「设计开发记录」那种"正文是目录表 + 又有子节点"的
            # 章节，正文(目录)会被埋掉只显示子节点。
            own = "\n".join((w.get("text") or "") for w in group)
            has_text = any(
                s and not re.match(r"#{1,6}\s", s)
                for s in (ln.strip() for ln in own.split("\n"))
            )
            out.append({
                "id": f"{doc_id}:{first.get('node_id', '')}",
                "title": first.get("title", ""),
                "page": page,
                "summary": summary or "",
                "windows": len(group),  # 合并了几个分页窗口（前端可标注"·N 段"）
                "has_text": has_text,
                "children": prune(merged_children),
            })
        return out

    return {
        "doc_id": doc_id,
        "doc_name": doc.get("doc_name", ""),
        "kb": doc.get("kb", "default"),
        "nodes": prune(doc.get("structure", []) or []),
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
    """删除文档：删 md + 原 PDF + workspace json，再用剩余 workspace 树轻量重建索引/目录。

    全程持 INDEX_LOCK：让"删文件 + 重建"原子，避免与并发入库交错（删一半被重建复活/丢文档）。
    重建走 rebuild_from_workspace（不重跑 VLM/不重新摘要），避免删一篇就把全库重新 LLM 摘要。
    """
    from app.modules.ingest.locks import INDEX_LOCK

    with INDEX_LOCK:
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

        # 轻量重建：用现成的 workspace 树重建索引/目录，不重跑 VLM/不重建树/不重新摘要
        # （删一篇不该让其余文档全部重新 LLM 摘要——那会让 delete 卡数分钟，表现为"删不掉"）
        from app.modules.ingest.tree_service import rebuild_from_workspace

        tree = rebuild_from_workspace()
        return {"deleted": doc_id, "doc_name": doc.get("doc_name", ""), "tree": tree}

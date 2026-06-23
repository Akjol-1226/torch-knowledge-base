"""据 docparse 落盘的 <md>.pagemap.json，为 PageIndex 树节点标注所在 PDF 页码。

docparse(core/docparse/convert.py) 入库时会在 md 同路径写 <md>.pagemap.json
（[[clean_line_1based, page], ...] 断点列表）。本模块把它读出来，按节点 line_num
查出 page 写进节点（node['page']），供 treestore 暴露给检索/上下文接口，前端据此
做「原文 PDF 跳第 N 页」。

为什么不直接匹配 PDF 文字定位页：火炬的 PDF 多为扫描件（纯图、无文字层），fitz
抽不出文本，页码只能来自 VLM 入库时打的页标记 → 即这份侧车。无侧车（历史文档/
裸 md 上传）时静默跳过，节点无 page，前端 PDF 跳页降级为打开首页。
"""

import json
from pathlib import Path

from app.core.logging import get_logger

log = get_logger("ingest.page_locator")


def _load_page_map(md_path: Path) -> list[tuple[int, int]] | None:
    sidecar = Path(str(md_path) + ".pagemap.json")
    if not sidecar.exists():
        return None
    try:
        runs = json.loads(sidecar.read_text(encoding="utf-8"))
        parsed = [(int(a), int(b)) for a, b in runs]
    except Exception:
        log.warning("pagemap_unreadable", md=str(md_path))
        return None
    return parsed or None


def _page_of(runs: list[tuple[int, int]], line_num: int) -> int:
    """runs 按 clean_line 升序；返回最后一个 start<=line_num 的 page。"""
    page = runs[0][1]
    for start, pg in runs:
        if start <= line_num:
            page = pg
        else:
            break
    return page


def page_span(md_path: str | Path, line_start: int, line_end: int, cap: int = 8) -> list[int]:
    """据 <md_path>.pagemap.json 把节点行范围 [line_start, line_end] 映射成页码列表。

    节点正文常跨多页,前端要逐页高亮;cap 限制最多返回多少页(避免巨型节点全文高亮)。
    无侧车返回 []。
    """
    runs = _load_page_map(Path(md_path))
    if not runs:
        return []
    p0 = _page_of(runs, line_start or 0)
    p1 = _page_of(runs, line_end or line_start or 0)
    if p1 < p0:
        p1 = p0
    return list(range(p0, min(p1, p0 + cap - 1) + 1))


def annotate_pages(md_path: str | Path, structure: list) -> int:
    """据 <md_path>.pagemap.json 给 structure 内每个节点写 node['page']（递归）。

    返回标注的节点数；无侧车则返回 0、不改 structure。
    """
    runs = _load_page_map(Path(md_path))
    if not runs:
        return 0
    count = 0

    def walk(nodes: list) -> None:
        nonlocal count
        for n in nodes:
            n["page"] = _page_of(runs, n.get("line_num", 0) or 0)
            count += 1
            walk(n.get("nodes") or [])

    walk(structure)
    return count

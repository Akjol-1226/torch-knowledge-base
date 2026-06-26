"""OCR 定位:对扫描件 PDF 逐页 OCR 出文字框,供「数据来源 → 原文 PDF」高亮被引用处。

为什么用 OCR:火炬 PDF 多为扫描件(无文字层),拿不到文字坐标。用 RapidOCR(ONNX,中文好)
对页图识别出 [文本, 框]。入库时跑一遍,落侧车 `<md>.ocr.json`(页号 → [{t,b}],b 为页内
归一化 bbox [x0,y0,x1,y1])。查询时(/chat/node)按被引用节点的标题去匹配该页文字框 → 高亮框。

RapidOCR 只在 ocr_document(入库)里惰性导入;load_ocr/rects_for_node 是纯函数,查询路径不依赖引擎。
"""

import json
import os
import re
from pathlib import Path

from app.core.fsutil import write_text_atomic
from app.core.logging import get_logger

log = get_logger("ingest.ocr")

_KEEP = re.compile(r"[0-9a-z一-鿿]+")


def _norm(s: str) -> str:
    return "".join(_KEEP.findall((s or "").lower()))


def _sidecar(md_path: str | Path) -> Path:
    return Path(str(md_path) + ".ocr.json")


# ---------------- 入库:OCR 落侧车 ----------------
def _enable_cuda_dlls() -> None:
    """把 nvidia pip 库(cudnn/cublas/...)的 bin 目录塞进 PATH,让 onnxruntime CUDA EP 加载依赖 DLL。
    Windows 上 os.add_dll_directory 对传递依赖不可靠,必须改 PATH。无 nvidia 包则跳过(走 CPU)。"""
    try:
        import glob

        import nvidia
    except ImportError:
        return
    dirs: list[str] = []
    for base in list(getattr(nvidia, "__path__", [])):
        dirs += glob.glob(os.path.join(base, "*", "bin"))
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def ocr_document(pdf_path: str | Path, dpi: int = 200) -> dict[str, list[dict]]:
    """逐页 OCR,返回 {页号(str): [{"t": 文本, "b": [x0,y0,x1,y1] 归一化}, ...]}。

    有 onnxruntime-gpu + CUDA 时走 GPU(快很多),否则自动回退 CPU。
    """
    _enable_cuda_dlls()
    import fitz
    import numpy as np
    import onnxruntime as ort
    from rapidocr_onnxruntime import RapidOCR

    from app.core.config import get_settings

    # 默认 GPU；OCR_USE_GPU=false 强制 CPU。无 CUDA 库时即便为 True 也会自动回退 CPU。
    use_cuda = (
        get_settings().ocr_use_gpu
        and "CUDAExecutionProvider" in ort.get_available_providers()
    )
    engine = RapidOCR(det_use_cuda=use_cuda, cls_use_cuda=use_cuda, rec_use_cuda=use_cuda)
    s = get_settings()
    # 提取调参：低 box_thresh 召回faint文字、高 unclip_ratio 框住完整字形（低清扫描友好）
    ocr_kw = {"box_thresh": s.ocr_box_thresh, "unclip_ratio": s.ocr_unclip_ratio}
    min_score = s.ocr_min_score
    log.info("ocr_engine", device="cuda" if use_cuda else "cpu", dpi=dpi, **ocr_kw)
    out: dict[str, list[dict]] = {}
    doc = fitz.open(str(pdf_path))
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:  # RGBA → RGB
                img = img[:, :, :3]
            result, _ = engine(img, **ocr_kw)
            w, h = pix.width, pix.height
            boxes: list[dict] = []
            for quad, text, score in result or []:
                if not (text or "").strip():
                    continue
                sc = float(score) if score is not None else 1.0
                if sc < min_score:  # 滤掉低置信度框（印章/噪声乱框，避免误匹配）
                    continue
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                boxes.append({
                    "t": text,
                    "b": [round(min(xs) / w, 4), round(min(ys) / h, 4),
                          round(max(xs) / w, 4), round(max(ys) / h, 4)],
                    "s": round(sc, 3),  # rec 置信度，匹配时可据此进一步滤噪
                })
            out[str(i)] = boxes
    finally:
        doc.close()
    return out


def write_ocr_sidecar(pdf_path: str | Path, md_path: str | Path, dpi: int | None = None) -> int:
    """OCR pdf_path,把结果写到 <md_path>.ocr.json。返回总文字框数。

    dpi 缺省取 settings.ocr_render_dpi（默认 200；只为画高亮框，不需高清，GPU 上快很多）。
    """
    if dpi is None:
        from app.core.config import get_settings

        dpi = get_settings().ocr_render_dpi
    data = ocr_document(pdf_path, dpi=dpi)
    write_text_atomic(_sidecar(md_path), json.dumps(data, ensure_ascii=False))
    return sum(len(v) for v in data.values())


# ---------------- 查询:匹配被引用处 ----------------
def load_ocr(md_path: str | Path) -> dict | None:
    f = _sidecar(md_path)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


_MAX_RECTS = 60
_GAP_X = 0.02   # 横向就近合并阈值(页宽比例)
_GAP_Y = 0.035  # 纵向就近合并阈值(页高比例;行间距比横向大)
_NGRAM = 4
_FUZZY_RATIO = 0.6   # 长框:与正文【某一行】4-gram 重叠率达此值即命中(按行比,避开跨节点巧合)
_MARGIN = 0.07       # 上下页边带(页眉/页脚)占页高比例
_BOILER_MIN_PAGES = 3  # 在多少页的边带重复出现即判为页眉/页脚噪声 → 匹配时排除


def _grams(s: str) -> set:
    if len(s) < _NGRAM:
        return {s} if s else set()
    return {s[i:i + _NGRAM] for i in range(len(s) - _NGRAM + 1)}


def _line_gramsets(text: str) -> list[set]:
    """正文按行(\n)拆,每行算一个 gram 集合。匹配时框只需贴近【某一行】即可,
    而不是跟整节点的 gram 大袋比——后者会让短框靠巧合命中。"""
    out = []
    for ln in (text or "").split("\n"):
        n = _norm(ln)
        if len(n) >= 3:
            out.append(_grams(n))
    return out


def _boilerplate(ocr_data: dict) -> set:
    """跨多页在上下边带重复出现的归一化文本(标题/页眉/页脚/页码) → 匹配时排除,避免每页蹭命中。"""
    from collections import Counter

    cnt: Counter = Counter()
    for boxes in ocr_data.values():
        seen = set()
        for bx in boxes or []:
            y0, y1 = bx["b"][1], bx["b"][3]
            if y1 <= _MARGIN or y0 >= 1 - _MARGIN:  # 只看上下边带
                n = _norm(bx["t"])
                if len(n) >= 2 and n not in seen:
                    seen.add(n)
                    cnt[n] += 1
    return {t for t, c in cnt.items() if c >= _BOILER_MIN_PAGES}


def _merge_boxes(boxes: list[list[float]]) -> list[list[float]]:
    """把就近的小框迭代合并成大块:两框各自外扩 _GAP 后相交则并成一个外接矩形。

    密集命中(如整张表格的单元格)会并成一个大框 → "框住整块";相隔远的段落仍各成一块。
    """
    rects = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[float]] = []
        for r in rects:
            for o in out:
                if not (r[0] > o[2] + _GAP_X or r[2] < o[0] - _GAP_X
                        or r[1] > o[3] + _GAP_Y or r[3] < o[1] - _GAP_Y):
                    o[0] = min(o[0], r[0])
                    o[1] = min(o[1], r[1])
                    o[2] = max(o[2], r[2])
                    o[3] = max(o[3], r[3])
                    changed = True
                    break
            else:
                out.append(list(r))
        rects = out
    return rects


def rects_for_node(ocr_data: dict, sources: list) -> list[dict]:
    """把被引用内容匹配到的 OCR 文字框**就近合并成大块**后返回
    [{page,x0,y0,x1,y1}]（页内归一化坐标）。匹配不到返回 []（前端只跳页不高亮）。

    sources: list[(text, pages)] —— 被引用节点正文 + 其附表/子节点正文（各自只在自己的页上匹配，
    且各用自己的文本作 gram 比对，不混成一个大袋）。**不匹配标题**（否则常只框住标题）。
    每页对每个 OCR 框判命中：
    - 排除页眉/页脚噪声；
    - 框文本是正文子串（短而准，保留 245℃ 这类短数值）；或
    - 框较长(≥6)且与正文【某一行】的 4-gram 重叠率够高 → 容忍 OCR 错字，长句/表格行不再整框落空。
    合并:同页内就近聚块 → 整张表/整段被命中时画一个大框,而非几十个小框。
    """
    if not ocr_data or not sources:
        return []
    boiler = _boilerplate(ocr_data)
    by_page: dict[int, list[list[float]]] = {}
    for text, pages in sources:
        snorm = _norm(text)
        if len(snorm) < 4:
            continue
        line_sets = _line_gramsets(text)
        for p in [p for p in (pages or []) if p]:
            for bx in ocr_data.get(str(p)) or []:
                bn = _norm(bx["t"])
                if len(bn) < 3 or bn in boiler:
                    continue
                hit = bn in snorm
                if not hit and len(bn) >= 6:
                    bg = _grams(bn)
                    if bg and any(len(bg & lg) / len(bg) >= _FUZZY_RATIO for lg in line_sets):
                        hit = True
                if hit:
                    by_page.setdefault(p, []).append(bx["b"])

    out: list[dict] = []
    for p in sorted(by_page):
        for m in _merge_boxes(by_page[p]):
            out.append({"page": p, "x0": round(m[0], 4), "y0": round(m[1], 4),
                        "x1": round(m[2], 4), "y1": round(m[3], 4)})
            if len(out) >= _MAX_RECTS:
                return out
    return out

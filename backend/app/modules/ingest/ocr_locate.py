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

from app.core.logging import get_logger

log = get_logger("ingest.ocr")

_KEEP = re.compile(r"[0-9a-z一-鿿]+")


def _norm(s: str) -> str:
    return "".join(_KEEP.findall((s or "").lower()))


def _sidecar(md_path: str | Path) -> Path:
    return Path(str(md_path) + ".ocr.json")


# ---------------- 入库:OCR 落侧车 ----------------
def _enable_cuda_dlls() -> None:
    """把 nvidia pip 库(cudnn/cublas/...)的 bin 目录塞进 PATH,让 onnxruntime CUDA EP 能加载依赖 DLL。
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


def ocr_document(pdf_path: str | Path, dpi: int = 500) -> dict[str, list[dict]]:
    """逐页 OCR,返回 {页号(str): [{"t": 文本, "b": [x0,y0,x1,y1] 归一化}, ...]}。

    有 onnxruntime-gpu + CUDA 时走 GPU(快很多),否则自动回退 CPU。
    """
    _enable_cuda_dlls()
    import fitz
    import numpy as np
    import onnxruntime as ort
    from rapidocr_onnxruntime import RapidOCR

    use_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    engine = RapidOCR(det_use_cuda=use_cuda, cls_use_cuda=use_cuda, rec_use_cuda=use_cuda)
    log.info("ocr_engine", device="cuda" if use_cuda else "cpu", dpi=dpi)
    out: dict[str, list[dict]] = {}
    doc = fitz.open(str(pdf_path))
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:  # RGBA → RGB
                img = img[:, :, :3]
            result, _ = engine(img)
            w, h = pix.width, pix.height
            boxes: list[dict] = []
            for quad, text, score in result or []:
                if not (text or "").strip():
                    continue
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                boxes.append({
                    "t": text,
                    "b": [round(min(xs) / w, 4), round(min(ys) / h, 4),
                          round(max(xs) / w, 4), round(max(ys) / h, 4)],
                })
            out[str(i)] = boxes
    finally:
        doc.close()
    return out


def write_ocr_sidecar(pdf_path: str | Path, md_path: str | Path, dpi: int = 500) -> int:
    """OCR pdf_path,把结果写到 <md_path>.ocr.json。返回总文字框数。"""
    data = ocr_document(pdf_path, dpi=dpi)
    _sidecar(md_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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
                    o[0] = min(o[0], r[0]); o[1] = min(o[1], r[1])
                    o[2] = max(o[2], r[2]); o[3] = max(o[3], r[3])
                    changed = True
                    break
            else:
                out.append(list(r))
        rects = out
    return rects


def rects_for_node(ocr_data: dict, pages: list[int], title: str, text: str = "") -> list[dict]:
    """匹配被引用节点的标题**和正文**,把命中的 OCR 小框**就近合并成大块**后返回
    [{page,x0,y0,x1,y1}]（页内归一化坐标）。匹配不到返回 []（前端只跳页不高亮）。

    - 标题:只在首页找(标题框置信度高、字串与标题高度重合)。
    - 正文:节点常跨多页,逐页把"框文本出现在节点正文里"的框都收集。
    - 合并:同页内就近聚块 → 整张表格/整段被命中时只画一个大框,而不是几十个小框。
    """
    pages = [p for p in (pages or []) if p]
    if not ocr_data or not pages:
        return []
    by_page: dict[int, list[list[float]]] = {}

    tnorm = _norm(title)
    if len(tnorm) >= 3:  # 标题:首页
        for bx in ocr_data.get(str(pages[0])) or []:
            bn = _norm(bx["t"])
            if len(bn) >= 4 and (bn in tnorm or tnorm in bn):
                by_page.setdefault(pages[0], []).append(bx["b"])

    bnorm = _norm(text)
    if len(bnorm) >= 4:  # 正文:每页
        for p in pages:
            for bx in ocr_data.get(str(p)) or []:
                bn = _norm(bx["t"])
                if len(bn) >= 3 and bn in bnorm:
                    by_page.setdefault(p, []).append(bx["b"])

    out: list[dict] = []
    for p in pages:
        for m in _merge_boxes(by_page.get(p, [])):
            out.append({"page": p, "x0": round(m[0], 4), "y0": round(m[1], 4),
                        "x1": round(m[2], 4), "y1": round(m[3], 4)})
            if len(out) >= _MAX_RECTS:
                return out
    return out

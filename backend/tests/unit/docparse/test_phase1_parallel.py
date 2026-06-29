"""Phase 1 并行化：逐页独立处理，不再维护/传递标题栈或 previous_page_tail。"""
from unittest.mock import patch

import fitz

from app.core.docparse.models import DocumentContext, Heading, PageStructure
from app.core.docparse.outline import run_phase1


def _make_pdf(tmp_path, n):
    pdf = tmp_path / "t.pdf"
    doc = fitz.open()
    for i in range(n):
        p = doc.new_page()
        p.insert_text((72, 72), f"第 {i+1} 页正文内容，用于满足文本层。" * 2)
    doc.save(str(pdf))
    doc.close()
    imgs = []
    for i in range(n):
        ip = tmp_path / f"page_{i+1:03d}.png"
        ip.write_bytes(b"x")
        imgs.append(ip)
    return str(pdf), imgs


def test_phase1_processes_all_pages_without_stack(tmp_path):
    pdf, imgs = _make_pdf(tmp_path, 5)
    ctx = DocumentContext(pdf_path=pdf, file_title="t", total_pages=5)
    seen = []

    def fake_vlm(image_path, file_title, page_no, total_pages,
                 previous_page_tail, heading_stack=None):
        seen.append((page_no, previous_page_tail, heading_stack))
        return PageStructure(
            page_no=page_no,
            headings=[Heading(text=f"标题{page_no}", level=2)],
            extraction_method="vlm",
        )

    with patch("app.core.docparse.outline.extract_page_structure_vlm", side_effect=fake_vlm):
        result, raw = run_phase1(pdf, imgs, ctx)

    # 所有页都被处理
    assert set(result.page_structures) == {1, 2, 3, 4, 5}
    assert all(result.page_structures[p].headings for p in range(1, 6))
    # 并行：每页都不传标题栈、不传 previous_page_tail（逐页独立）
    assert len(seen) == 5
    for pno, tail, stack in seen:
        assert tail is None, f"page {pno} 仍传了 previous_page_tail"
        assert stack is None, f"page {pno} 仍传了 heading_stack"


def test_phase1_per_page_failure_isolated(tmp_path):
    """某页 VLM 失败 → 该页空结构，其余页不受影响（并行无级联）。"""
    pdf, imgs = _make_pdf(tmp_path, 4)
    ctx = DocumentContext(pdf_path=pdf, file_title="t", total_pages=4)

    def fake_vlm(image_path, file_title, page_no, total_pages,
                 previous_page_tail, heading_stack=None):
        if page_no == 2:
            raise RuntimeError("VLM down on page 2")
        return PageStructure(page_no=page_no,
                             headings=[Heading(text=f"标题{page_no}", level=2)],
                             extraction_method="vlm")

    with patch("app.core.docparse.outline.extract_page_structure_vlm", side_effect=fake_vlm):
        result, raw = run_phase1(pdf, imgs, ctx)

    assert set(result.page_structures) == {1, 2, 3, 4}
    assert not result.page_structures[2].headings          # 失败页为空
    assert result.page_structures[1].headings              # 其余页正常
    assert result.page_structures[3].headings

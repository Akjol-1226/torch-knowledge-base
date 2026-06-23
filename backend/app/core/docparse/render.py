from pathlib import Path
import fitz


def render_pdf_to_images(pdf_path: str, dpi: int = 400, output_dir: Path | None = None) -> list[Path]:
    """Render every page of a PDF to PNG. Returns list of image paths (1-indexed by filename)."""
    pdf_path_obj = Path(pdf_path)
    if output_dir is None:
        output_dir = pdf_path_obj.parent / "_debug" / "pages_img"
    output_dir.mkdir(parents=True, exist_ok=True)

    images: list[Path] = []
    doc = fitz.open(str(pdf_path_obj))
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            img_path = output_dir / f"page_{i + 1:03d}.png"
            pix.save(str(img_path))
            images.append(img_path)
    finally:
        doc.close()
    return images

from __future__ import annotations

from pathlib import Path


class PdfRenderError(ValueError):
    """PDF cannot safely be used as one FastCAM split-drawing input."""


def extract_pdf_text_pages(path: Path) -> list[str]:
    """Read the embedded FastNEST text before falling back to OCR.

    FastNEST PDFs normally contain selectable text.  That text preserves the
    part list and the marked plate weight far more reliably than OCR, even
    when a page has a busy nesting drawing.
    """
    try:
        import fitz  # PyMuPDF
        document = fitz.open(path)
    except Exception as exc:
        raise PdfRenderError(f"PDF 无法打开：{exc}") from exc

    try:
        pages = [document.load_page(index).get_text("text") for index in range(document.page_count)]
        if not pages:
            raise PdfRenderError("PDF 不含可识别页面")
        return pages
    finally:
        document.close()


def render_pdf_pages(path: Path, output_dir: Path, dpi: int = 400) -> list[Path]:
    """Render every PDF page to a lossless PNG for the existing OCR pipeline."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency checked in CI
        raise PdfRenderError("PDF 渲染组件未安装") from exc

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfRenderError(f"PDF 无法打开：{exc}") from exc

    try:
        scale = dpi / 72
        output_dir.mkdir(parents=True, exist_ok=True)
        pages: list[Path] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            output_path = output_dir / f"page-{index + 1}.png"
            pixmap.save(output_path)
            pages.append(output_path)
        if not pages:
            raise PdfRenderError("PDF 不含可识别页面")
        return pages
    except PdfRenderError:
        raise
    except Exception as exc:
        raise PdfRenderError(f"PDF 渲染失败：{exc}") from exc
    finally:
        document.close()

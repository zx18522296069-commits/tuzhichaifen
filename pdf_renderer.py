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


def render_pdf_page(path: Path, output_dir: Path, page_index: int, dpi: int = 400) -> Path:
    """Render one zero-based PDF page to a lossless PNG.

    Multi-page PDFs use this helper so a page with good embedded text never
    gets sent through OCR merely because another page needs OCR fallback.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency checked in CI
        raise PdfRenderError("PDF 渲染组件未安装") from exc

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfRenderError(f"PDF 无法打开：{exc}") from exc

    try:
        if page_index < 0 or page_index >= document.page_count:
            raise PdfRenderError(f"PDF 页码越界：{page_index + 1}")
        scale = dpi / 72
        output_dir.mkdir(parents=True, exist_ok=True)
        page = document.load_page(page_index)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        output_path = output_dir / f"page-{page_index + 1}.png"
        pixmap.save(output_path)
        return output_path
    except PdfRenderError:
        raise
    except Exception as exc:
        raise PdfRenderError(f"PDF 第 {page_index + 1} 页渲染失败：{exc}") from exc
    finally:
        document.close()


def render_pdf_pages(path: Path, output_dir: Path, dpi: int = 400) -> list[Path]:
    """Render every PDF page to a lossless PNG for compatibility/tests."""
    try:
        import fitz  # PyMuPDF
        document = fitz.open(path)
    except Exception as exc:
        raise PdfRenderError(f"PDF 无法打开：{exc}") from exc

    try:
        page_count = document.page_count
        if not page_count:
            raise PdfRenderError("PDF 不含可识别页面")
    finally:
        document.close()

    return [render_pdf_page(path, output_dir, index, dpi=dpi) for index in range(page_count)]

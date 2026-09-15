from __future__ import annotations

from pathlib import Path


class PdfRenderError(ValueError):
    """PDF cannot safely be used as one FastCAM split-drawing input."""


def render_single_page_pdf(path: Path, output_path: Path, dpi: int = 400) -> Path:
    """Render one PDF page to a lossless PNG for the existing OCR pipeline.

    A PDF is accepted only when it contains exactly one page.  Treating a
    multi-page PDF as one steel plate would make its board number ambiguous,
    so it must be split by the operator before it can be processed.
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
        if document.page_count != 1:
            raise PdfRenderError(
                f"PDF 共 {document.page_count} 页；一份待拆 PDF 只能对应一张板材，"
                "请先按板材拆成单页 PDF 后重新执行"
            )
        page = document.load_page(0)
        scale = dpi / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(output_path)
        return output_path
    except PdfRenderError:
        raise
    except Exception as exc:
        raise PdfRenderError(f"PDF 渲染失败：{exc}") from exc
    finally:
        document.close()

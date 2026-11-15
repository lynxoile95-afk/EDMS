"""PDF/DOCX extraction for both legacy search and Persian file QA."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.extraction.ocr import get_ocr_engine
from app.search.text_normalizer import normalize_visible_text
from app.services import pdf_utils


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_no: int
    text: str
    source_type: str
    ocr_confidence: float | None = None
    error: str | None = None


def _safe_dpi(width_pt: float, height_pt: float, requested_dpi: int, max_pixels: int) -> int:
    """Keep an OCR raster within the configured pixel budget."""

    if width_pt <= 0 or height_pt <= 0 or max_pixels <= 0:
        return max(72, min(requested_dpi, 300))
    max_dpi = math.sqrt(max_pixels / (width_pt * height_pt)) * 72.0
    return max(72, min(int(requested_dpi), int(max_dpi)))


def extract_pdf_pages(
    path: str,
    *,
    ocr_enabled: bool | None = None,
    ocr_langs: str = "eng+fas",
    ocr_dpi: int | None = None,
    max_pixels: int | None = None,
) -> list[ExtractedPage]:
    """Return one result for every PDF page; isolate errors to that page.

    OCR remains lazy and follows the existing SEARCH_INDEXING/OCR flags.  The
    native PDF path is unchanged for the legacy 800-character index.
    """

    use_ocr = settings.ocr_enabled if ocr_enabled is None else ocr_enabled
    dpi = settings.ocr_dpi if ocr_dpi is None else ocr_dpi
    pixels = settings.ocr_max_pixels if max_pixels is None else max_pixels
    document = pdf_utils.open_document(path)
    pages: list[ExtractedPage] = []
    try:
        for index in range(document.page_count):
            page_no = index + 1
            try:
                page = document.load_page(index)
                native = (page.get_text("text") or "").strip()
                if len(native) >= 2:
                    pages.append(ExtractedPage(page_no, native, "native"))
                    continue
                if not use_ocr:
                    pages.append(ExtractedPage(page_no, native, "native"))
                    continue

                bounded_dpi = _safe_dpi(float(page.rect.width), float(page.rect.height), dpi, pixels)
                image = pdf_utils.render_png(path, page_no, dpi=bounded_dpi)
                text, confidence = get_ocr_engine().read_image(image, ocr_langs)
                pages.append(
                    ExtractedPage(
                        page_no,
                        (text or "").strip(),
                        "ocr" if text else "native",
                        confidence if text else None,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one damaged page must not stop indexing
                pages.append(ExtractedPage(page_no, "", "native", error=str(exc)[:500]))
    finally:
        document.close()
    return pages


def extract_docx_blocks(path: str | Path) -> list[tuple[str, str]]:
    """Extract non-empty paragraphs and table rows from a DOCX.

    ``python-docx`` is imported only when a DOCX is actually indexed.  This
    keeps SQLite/legacy PDF startup safe when the optional dependency is absent.
    """

    try:
        import docx  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is installed in production
        raise RuntimeError("برای فهرست‌سازی DOCX، بسته python-docx نصب نشده است.") from exc

    document = docx.Document(str(path))
    blocks: list[tuple[str, str]] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = normalize_visible_text(paragraph.text)
        if text:
            blocks.append((f"paragraph:{index}", text))
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            cells = [normalize_visible_text(cell.text) for cell in row.cells]
            text = " | ".join(cell for cell in cells if cell)
            if text:
                blocks.append((f"table:{table_index}:row:{row_index}", text))
    return blocks


def extract_pdf_blocks(path: str | Path) -> list[tuple[str, str]]:
    """Extract native PDF text with stable ``page:N`` provenance.

    Prefer pypdf, as in the reference implementation.  PyMuPDF remains the
    fallback used by the existing EDMS deployment and can additionally provide
    OCR for the RAG index through ``extract_pdf_pages``.
    """

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        pages = extract_pdf_pages(str(path), ocr_enabled=False)
        return [
            (f"page:{page.page_no}", normalize_visible_text(page.text))
            for page in pages
            if normalize_visible_text(page.text)
        ]

    blocks: list[tuple[str, str]] = []
    for index, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = normalize_visible_text(page.extract_text() or "")
        if text:
            blocks.append((f"page:{index}", text))
    return blocks


def extract_document_blocks(path: str | Path) -> list[tuple[str, str]]:
    """Dispatch the versioned RAG extractor by extension."""

    document_path = Path(path)
    suffix = document_path.suffix.lower()
    if suffix == ".docx":
        return extract_docx_blocks(document_path)
    if suffix == ".pdf":
        return extract_pdf_blocks(document_path)
    raise ValueError("فقط فایل‌های DOCX و PDF برای پرسش از فایل پشتیبانی می‌شوند.")


__all__ = [
    "ExtractedPage",
    "extract_pdf_pages",
    "extract_docx_blocks",
    "extract_pdf_blocks",
    "extract_document_blocks",
]

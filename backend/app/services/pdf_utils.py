"""Thin, testable wrapper over PyMuPDF (imported lazily; optional at runtime)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field


class PdfError(RuntimeError):
    pass


@dataclass(slots=True)
class PageInfo:
    page_no: int          # 1-based
    width_pt: float
    height_pt: float
    has_text: bool
    text_chars: int
    image_count: int

    @property
    def aspect_ratio(self) -> float:
        return round(self.width_pt / self.height_pt, 4) if self.height_pt else 0.0


@dataclass(slots=True)
class PdfInfo:
    page_count: int
    is_encrypted: bool
    is_scanned: bool
    pages: list[PageInfo] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class Word:
    text: str
    bbox: tuple[float, float, float, float]
    block: int
    line: int
    word_no: int
    page_no: int

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def _fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise PdfError("PyMuPDF (pymupdf) is not installed in this runtime.") from exc


def open_document(path: str):
    fitz = _fitz()
    try:
        return fitz.open(path)
    except Exception as exc:  # pragma: no cover - corrupt files
        raise PdfError(f"فایل PDF قابل باز کردن نیست: {exc}") from exc


def inspect_pdf(path: str) -> PdfInfo:
    """Structural facts needed by the ingest worker."""
    doc = open_document(path)
    try:
        pages: list[PageInfo] = []
        encrypted = bool(doc.is_encrypted and doc.needs_pass)
        for index, page in enumerate(doc):
            text = page.get_text("text") or ""
            images = len(page.get_images(full=True))
            pages.append(
                PageInfo(
                    page_no=index + 1,
                    width_pt=float(page.rect.width),
                    height_pt=float(page.rect.height),
                    has_text=len(text.strip()) > 20,
                    text_chars=len(text.strip()),
                    image_count=images,
                )
            )
        scanned = bool(pages) and not any(p.has_text for p in pages)
        metadata = {k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str)}
        return PdfInfo(
            page_count=len(pages), is_encrypted=encrypted, is_scanned=scanned,
            pages=pages, metadata=metadata,
        )
    finally:
        doc.close()


def extract_words(path: str, page_no: int) -> list[Word]:
    """Native text layer with coordinates (1-based page number)."""
    doc = open_document(path)
    try:
        page = doc.load_page(page_no - 1)
        words: list[Word] = []
        for x0, y0, x1, y1, text, block, line, word_no in page.get_text("words"):
            if text and text.strip():
                words.append(
                    Word(
                        text=text.strip(),
                        bbox=(float(x0), float(y0), float(x1), float(y1)),
                        block=block,
                        line=line,
                        word_no=word_no,
                        page_no=page_no,
                    )
                )
        return words
    finally:
        doc.close()


def page_text(path: str, page_no: int) -> str:
    doc = open_document(path)
    try:
        return doc.load_page(page_no - 1).get_text("text") or ""
    finally:
        doc.close()


def render_png(
    path: str,
    page_no: int,
    dpi: int = 200,
    clip: tuple[float, float, float, float] | None = None,
) -> bytes:
    """Rasterise a page (or a clip rectangle in points) to PNG bytes."""
    doc = open_document(path)
    try:
        page = doc.load_page(page_no - 1)
        matrix = _fitz().Matrix(dpi / 72, dpi / 72)
        pixmap = page.get_pixmap(matrix=matrix, clip=_fitz().Rect(*clip) if clip else None, alpha=False)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def render_region_norm(
    path: str,
    page_no: int,
    region: dict,   # {"x":0.6,"y":0.7,"w":0.35,"h":0.25} (normalised)
    dpi: int = 300,
) -> bytes:
    """Crop a normalised rectangle of a page and render it at `dpi`."""
    doc = open_document(path)
    try:
        page = doc.load_page(page_no - 1)
        width, height = page.rect.width, page.rect.height
        x0 = max(0.0, min(1.0, float(region.get("x", 0)))) * width
        y0 = max(0.0, min(1.0, float(region.get("y", 0)))) * height
        w = max(0.0, min(1.0, float(region.get("w", 1)))) * width
        h = max(0.0, min(1.0, float(region.get("h", 1)))) * height
        clip = (x0, y0, min(width, x0 + w), min(height, y0 + h))
        return render_png(path, page_no, dpi=dpi, clip=clip)
    finally:
        doc.close()


def crop_words_to_region(words: list[Word], region: dict, page_width: float, page_height: float) -> list[Word]:
    """Keep only words whose centre lies inside the normalised region."""
    x0 = float(region.get("x", 0)) * page_width
    y0 = float(region.get("y", 0)) * page_height
    x1 = x0 + float(region.get("w", 1)) * page_width
    y1 = y0 + float(region.get("h", 1)) * page_height
    return [w for w in words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]


def split_pdf_pages(path: str, out_dir: str, prefix: str = "page") -> list[str]:
    """Write each page to its own PDF; returns file paths in page order."""
    import os

    fitz = _fitz()
    os.makedirs(out_dir, exist_ok=True)
    paths: list[str] = []
    source = open_document(path)
    try:
        for index in range(source.page_count):
            single = fitz.open()
            try:
                single.insert_pdf(source, from_page=index, to_page=index)
                target = os.path.join(out_dir, f"{prefix}-{index + 1:04d}.pdf")
                single.save(target)
                paths.append(target)
            finally:
                single.close()
    finally:
        source.close()
    return paths


def image_to_png_bytes(image_bytes: bytes, max_dimension: int = 1600) -> bytes:
    """Downscale an image for VLM input (keeps tokens/cost predictable)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return image_bytes
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if max(image.size) > max_dimension:
            ratio = max_dimension / max(image.size)
            image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:  # pragma: no cover
        return image_bytes

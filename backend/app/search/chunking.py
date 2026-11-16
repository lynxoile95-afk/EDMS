"""Deterministic page-aware chunking for mixed Persian/English PDF text."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_NEWLINES_RE = re.compile(r"\r\n?|\u2028|\u2029")


@dataclass(frozen=True, slots=True)
class PageText:
    page_no: int
    text: str
    source_type: str = "native"
    ocr_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TextChunk:
    page_no: int
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    source_type: str
    ocr_confidence: float | None = None


def normalize_search_text(text: str) -> str:
    """Normalise layout noise without transliterating Persian or English.

    NFKC fixes common PDF ligature/full-width differences; the zero-width
    non-joiner is intentionally retained because it is meaningful in Persian.
    Newlines remain paragraph boundaries while runs of horizontal whitespace are
    collapsed. This same representation is stored, embedded, and highlighted.
    """

    value = unicodedata.normalize("NFKC", text or "")
    value = _NEWLINES_RE.sub("\n", value)
    value = _WHITESPACE_RE.sub(" ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _boundary_before(text: str, start: int, proposed_end: int, minimum: int) -> int:
    """Find a useful word/line boundary without making tiny chunks."""

    if proposed_end >= len(text):
        return len(text)
    floor = min(proposed_end, start + max(1, minimum))
    candidates = [text.rfind("\n", floor, proposed_end), text.rfind(" ", floor, proposed_end)]
    boundary = max(candidates)
    return boundary if boundary > start + 1 else proposed_end


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[TextChunk]:
    """Split one page into approximately ``max_chars`` chunks.

    Chunks never cross a page because page number is attached by
    :func:`chunk_pages`. The overlap is character based and deterministic; a
    whitespace/sentence boundary is preferred when it does not create a very
    short chunk. Inputs are bounded by callers before embedding, but the
    function itself also rejects invalid parameters to avoid infinite loops.
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be between zero and max_chars - 1")
    normalized = normalize_search_text(text)
    if not normalized:
        return []

    result: list[TextChunk] = []
    start = 0
    chunk_index = 0
    minimum_boundary = max(40, max_chars // 2)
    while start < len(normalized):
        proposed_end = min(len(normalized), start + max_chars)
        end = _boundary_before(normalized, start, proposed_end, minimum_boundary)
        content = normalized[start:end].strip()
        if content:
            # Store source offsets against the normalised page text. Consumers
            # use page_no for PDF navigation; offsets are for audit/debug only.
            left_trim = len(normalized[start:end]) - len(normalized[start:end].lstrip())
            right_trim = len(normalized[start:end]) - len(normalized[start:end].rstrip())
            content_start = start + left_trim
            content_end = end - right_trim
            result.append(
                TextChunk(
                    page_no=1,
                    chunk_index=chunk_index,
                    content=content,
                    char_start=content_start,
                    char_end=content_end,
                    source_type="native",
                )
            )
            chunk_index += 1
        if end >= len(normalized):
            break
        next_start = max(start + 1, end - overlap)
        # Avoid repeatedly starting on a long run of whitespace while retaining
        # the requested approximate overlap.
        while next_start < len(normalized) and normalized[next_start].isspace():
            next_start += 1
        start = next_start
    return result


def chunk_pages(
    pages: Mapping[int, str | PageText] | Iterable[PageText],
    max_chars: int = 800,
    overlap: int = 100,
) -> list[TextChunk]:
    """Chunk each page independently and preserve native/OCR provenance."""

    if isinstance(pages, Mapping):
        page_items = [
            PageText(page_no=int(page_no), text=value.text if isinstance(value, PageText) else str(value))
            if not isinstance(value, PageText)
            else value
            for page_no, value in pages.items()
        ]
    else:
        page_items = list(pages)

    chunks: list[TextChunk] = []
    for page in sorted(page_items, key=lambda item: item.page_no):
        page_chunks = chunk_text(page.text, max_chars=max_chars, overlap=overlap)
        for item in page_chunks:
            chunks.append(
                TextChunk(
                    page_no=page.page_no,
                    chunk_index=item.chunk_index,
                    content=item.content,
                    char_start=item.char_start,
                    char_end=item.char_end,
                    source_type=page.source_type,
                    ocr_confidence=page.ocr_confidence,
                )
            )
    return chunks

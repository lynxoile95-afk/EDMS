"""Versioned 220-word/45-word-overlap chunks for Persian file QA."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Sequence
from pathlib import Path
from typing import Iterable

from app.search.text_normalizer import normalize_farsi, normalize_visible_text


@dataclass(frozen=True, slots=True)
class Chunk:
    """A display-preserving chunk with document/page/paragraph provenance."""

    id: str
    text: str
    normalized_text: str
    source: str
    source_document: str
    source_path: str
    page_no: int | None = None


# A named alias makes the purpose clearer to callers while retaining the
# reference implementation's public ``Chunk`` shape.
RAGChunkText = Chunk


def _page_from_source(source: str) -> int | None:
    match = re.search(r"(?:^|[, ])page:(\d+)(?:$|[, ])", source)
    return int(match.group(1)) if match else None


def file_hash(path: Path) -> str:
    """Return the reference implementation's streaming SHA-256 file hash."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_from_index(data: dict[str, Any]) -> Chunk:
    """Rehydrate a stored/reference chunk record without exposing its path."""

    source_path = str(data.get("source_path", ""))
    source_document = str(data.get("source_document") or Path(source_path).name or "سند")
    source = str(data.get("source", ""))
    return Chunk(
        id=str(data.get("id", "")),
        text=str(data.get("text", "")),
        normalized_text=str(data.get("normalized_text", "")),
        source=source,
        source_document=source_document,
        source_path=source_path,
        page_no=_page_from_source(source),
    )


def cosine_similarity(query: Sequence[float], matrix: Sequence[Sequence[float]]) -> list[float]:
    """Calculate normalized cosine scores without requiring a model runtime."""

    query_values = [float(value) for value in query]
    query_norm = math.sqrt(sum(value * value for value in query_values)) or 1e-12
    scores: list[float] = []
    for row in matrix:
        values = [float(value) for value in row]
        row_norm = math.sqrt(sum(value * value for value in values)) or 1e-12
        scores.append(sum(a * b for a, b in zip(values, query_values)) / (row_norm * query_norm))
    return scores


def chunk_blocks(
    blocks: Iterable[tuple[str, str]],
    max_words: int = 220,
    overlap_words: int = 45,
    document_name: str = "",
    document_path: str = "",
    start_index: int = 1,
) -> list[Chunk]:
    """Combine extracted blocks into exact bounded word windows.

    The window is allowed to span adjacent paragraphs/table rows/pages so a
    question can use context crossing an extraction boundary.  Every source
    label represented in a window is retained in insertion order.  The final
    overlap-only window is deliberately not emitted; each emitted chunk adds at
    least one new source word.
    """

    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if overlap_words < 0 or overlap_words >= max_words:
        raise ValueError("overlap_words must be between zero and max_words - 1")

    words: list[str] = []
    sources: list[str] = []
    for source, text in blocks:
        visible = normalize_visible_text(text)
        for word in visible.split():
            words.append(word)
            sources.append(str(source))

    chunks: list[Chunk] = []
    if not words:
        return chunks

    step = max_words - overlap_words
    start = 0
    previous_end = -1
    while start < len(words):
        end = min(len(words), start + max_words)
        if end <= previous_end:
            break
        window_words = words[start:end]
        window_sources = list(dict.fromkeys(sources[start:end]))
        text = " ".join(window_words).strip()
        normalized = normalize_farsi(text)
        if normalized:
            source = ", ".join(window_sources)
            chunks.append(
                Chunk(
                    id=f"chunk-{start_index + len(chunks)}",
                    text=text,
                    normalized_text=normalized,
                    source=source,
                    source_document=document_name or Path(document_path).name or "سند",
                    source_path=document_path,
                    page_no=_page_from_source(source),
                )
            )
        previous_end = end
        if end >= len(words):
            break
        start += step

    return chunks


__all__ = [
    "Chunk",
    "RAGChunkText",
    "chunk_blocks",
    "chunk_from_index",
    "cosine_similarity",
    "file_hash",
    "normalize_farsi",
    "normalize_visible_text",
]

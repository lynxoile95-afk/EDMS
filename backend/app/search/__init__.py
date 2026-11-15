"""Opt-in content indexing and hybrid search services."""

from app.search.chunking import chunk_pages, chunk_text, normalize_search_text

__all__ = ["chunk_pages", "chunk_text", "normalize_search_text"]

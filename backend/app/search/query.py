"""Bound-parameter keyword, vector and reciprocal-rank-fusion queries."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, DocumentRevision, SourceFile
from app.search.embeddings import EmbeddingUnavailable, get_embedding_service

RRF_K = 60
BRANCH_LIMIT = 30


@dataclass(slots=True)
class SearchHit:
    chunk: DocumentChunk
    filename: str
    score: float
    keyword_rank: int | None = None
    semantic_rank: int | None = None


def current_revision_clause():
    """Allow unregistered files and exclude every non-current revision.

    A chunk made before registration has no ``revision_id``. Once any revision
    is registered for that file, that unversioned staging must not remain
    visible as a current document.
    """

    current_revision = exists(
        select(DocumentRevision.id).where(
            DocumentRevision.id == DocumentChunk.revision_id,
            DocumentRevision.is_current.is_(True),
        )
    )
    any_revision_for_file = exists(
        select(DocumentRevision.id).where(
            DocumentRevision.source_file_id == DocumentChunk.file_id,
        )
    )
    unregistered_file = and_(
        DocumentChunk.revision_id.is_(None),
        ~any_revision_for_file,
    )
    return or_(unregistered_file, current_revision)


def _scope_clause(project_ids: list[Any] | None, project_id: Any | None):
    clauses = [DocumentChunk.is_active.is_(True), current_revision_clause()]
    if project_id is not None:
        clauses.append(DocumentChunk.project_id == project_id)
    elif project_ids is not None:
        # An empty membership set must never mean "all projects".
        clauses.append(DocumentChunk.project_id.in_(project_ids) if project_ids else False)
    return and_(*clauses)


def build_keyword_query(
    query: str,
    *,
    project_ids: list[Any] | None = None,
    project_id: Any | None = None,
    source_type: str | None = None,
    include_old_revisions: bool = False,
):
    """Build the PostgreSQL simple-FTS/trigram branch without interpolating input."""

    clauses = [DocumentChunk.is_active.is_(True)]
    if not include_old_revisions:
        clauses.append(current_revision_clause())
    if project_id is not None:
        clauses.append(DocumentChunk.project_id == project_id)
    elif project_ids is not None:
        clauses.append(DocumentChunk.project_id.in_(project_ids) if project_ids else False)
    if source_type in {"native", "ocr"}:
        clauses.append(DocumentChunk.source_type == source_type)

    # ``simple`` is a fixed application constant, not user input; the query
    # text itself remains a bound parameter.
    ts_query = func.plainto_tsquery(literal_column("'simple'"), query)
    rank = func.ts_rank_cd(DocumentChunk.search_vector, ts_query)
    trigram = func.similarity(DocumentChunk.content, query)
    condition = or_(DocumentChunk.search_vector.op("@@")(ts_query), DocumentChunk.content.ilike(f"%{query}%"))
    return (
        select(DocumentChunk, SourceFile.original_filename, rank.label("keyword_score"))
        .join(SourceFile, SourceFile.id == DocumentChunk.file_id)
        .where(and_(*clauses), condition)
        .order_by(rank.desc(), trigram.desc(), DocumentChunk.created_at.desc())
        .limit(BRANCH_LIMIT)
    )


def build_sqlite_keyword_query(
    query: str,
    *,
    project_ids: list[Any] | None = None,
    project_id: Any | None = None,
    source_type: str | None = None,
    include_old_revisions: bool = False,
):
    """Build the Preview-safe bounded LIKE fallback."""

    clauses = [DocumentChunk.is_active.is_(True)]
    if not include_old_revisions:
        clauses.append(current_revision_clause())
    if project_id is not None:
        clauses.append(DocumentChunk.project_id == project_id)
    elif project_ids is not None:
        clauses.append(DocumentChunk.project_id.in_(project_ids) if project_ids else False)
    if source_type in {"native", "ocr"}:
        clauses.append(DocumentChunk.source_type == source_type)
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    # Each value is a SQLAlchemy bound parameter, including wildcard values.
    clauses.append(or_(*[DocumentChunk.content.ilike(f"%{term}%") for term in terms]))
    return (
        select(DocumentChunk, SourceFile.original_filename)
        .join(SourceFile, SourceFile.id == DocumentChunk.file_id)
        .where(and_(*clauses))
        .order_by(DocumentChunk.created_at.desc())
        .limit(BRANCH_LIMIT)
    )


def build_semantic_query(
    embedding: list[float],
    *,
    project_ids: list[Any] | None = None,
    project_id: Any | None = None,
    source_type: str | None = None,
    include_old_revisions: bool = False,
):
    clauses = [DocumentChunk.is_active.is_(True), DocumentChunk.embedding.is_not(None)]
    if not include_old_revisions:
        clauses.append(current_revision_clause())
    if project_id is not None:
        clauses.append(DocumentChunk.project_id == project_id)
    elif project_ids is not None:
        clauses.append(DocumentChunk.project_id.in_(project_ids) if project_ids else False)
    if source_type in {"native", "ocr"}:
        clauses.append(DocumentChunk.source_type == source_type)
    distance = DocumentChunk.embedding.op("<=>")(embedding)
    return (
        select(DocumentChunk, SourceFile.original_filename, distance.label("distance"))
        .join(SourceFile, SourceFile.id == DocumentChunk.file_id)
        .where(and_(*clauses))
        .order_by(distance.asc())
        .limit(BRANCH_LIMIT)
    )


def _rows(db: Session, statement):
    return list(db.execute(statement).all())


def _highlight_html(content: str, query: str, max_chars: int = 280) -> tuple[str, str]:
    """Return a safe plain snippet and marked-up equivalent."""

    raw = content or ""
    terms = [re.escape(part) for part in re.split(r"\s+", query.strip()) if part]
    pattern = re.compile("|".join(terms), re.IGNORECASE) if terms else None
    match = pattern.search(raw) if pattern else None
    if match:
        start = max(0, match.start() - max_chars // 3)
        end = min(len(raw), start + max_chars)
        snippet = raw[start:end]
        prefix = "…" if start else ""
        suffix = "…" if end < len(raw) else ""
    else:
        snippet = raw[:max_chars]
        prefix = ""
        suffix = "…" if len(raw) > max_chars else ""

    escaped_plain = html.escape(snippet)
    if not pattern:
        return f"{prefix}{snippet}{suffix}", f"{prefix}{escaped_plain}{suffix}"
    marked: list[str] = []
    cursor = 0
    for hit in pattern.finditer(snippet):
        marked.append(html.escape(snippet[cursor : hit.start()]))
        marked.append(f"<mark>{html.escape(snippet[hit.start() : hit.end()])}</mark>")
        cursor = hit.end()
    marked.append(html.escape(snippet[cursor:]))
    return f"{prefix}{snippet}{suffix}", f"{prefix}{''.join(marked)}{suffix}"


def search_chunks(
    db: Session,
    query: str,
    *,
    project_ids: list[Any] | None = None,
    project_id: Any | None = None,
    source_type: str | None = None,
    limit: int = 10,
    page: int = 1,
    include_old_revisions: bool = False,
) -> tuple[list[dict], bool, int]:
    """Run both top-30 branches and merge them with RRF ``k=60``."""

    backend = db.get_bind().dialect.name
    if backend == "sqlite":
        rows = _rows(
            db,
            build_sqlite_keyword_query(
                query,
                project_ids=project_ids,
                project_id=project_id,
                source_type=source_type,
                include_old_revisions=include_old_revisions,
            ),
        )
        merged: list[SearchHit] = [
            SearchHit(chunk=row[0], filename=row[1], score=1 / (RRF_K + rank))
            for rank, row in enumerate(rows, 1)
        ]
        semantic_used = False
    else:
        keyword_rows = _rows(
            db,
            build_keyword_query(
                query,
                project_ids=project_ids,
                project_id=project_id,
                source_type=source_type,
                include_old_revisions=include_old_revisions,
            ),
        )
        hits: dict[Any, SearchHit] = {}
        for rank, row in enumerate(keyword_rows, 1):
            chunk, filename = row[0], row[1]
            hits[chunk.id] = SearchHit(
                chunk=chunk,
                filename=filename,
                score=1 / (RRF_K + rank),
                keyword_rank=rank,
            )
        semantic_used = False
        try:
            query_embedding = get_embedding_service().embed_query(query)
            semantic_rows = _rows(
                db,
                build_semantic_query(
                    query_embedding,
                    project_ids=project_ids,
                    project_id=project_id,
                    source_type=source_type,
                    include_old_revisions=include_old_revisions,
                ),
            )
            semantic_used = True
            for rank, row in enumerate(semantic_rows, 1):
                chunk, filename = row[0], row[1]
                hit = hits.get(chunk.id)
                if hit is None:
                    hit = SearchHit(chunk=chunk, filename=filename, score=0.0)
                    hits[chunk.id] = hit
                hit.score += 1 / (RRF_K + rank)
                hit.semantic_rank = rank
        except EmbeddingUnavailable:
            # A healthy keyword search is preferable to making search unusable
            # while a model is downloading or unavailable.
            semantic_used = False
        merged = sorted(hits.values(), key=lambda item: (-item.score, str(item.chunk.id)))

    offset = max(0, page - 1) * limit
    selected = merged[offset : offset + limit]
    items: list[dict] = []
    for hit in selected:
        snippet, highlighted = _highlight_html(hit.chunk.content, query)
        items.append(
            {
                "chunk_id": str(hit.chunk.id),
                "file_id": str(hit.chunk.file_id),
                "project_id": str(hit.chunk.project_id),
                "filename": hit.filename,
                "page_no": hit.chunk.page_no,
                "source_type": hit.chunk.source_type,
                "snippet": snippet,
                "highlight": highlighted,
                "score": round(hit.score, 8),
                "keyword_rank": hit.keyword_rank,
                "semantic_rank": hit.semantic_rank,
                "page_url": f"/api/v1/files/source-files/{hit.chunk.file_id}#page={hit.chunk.page_no}",
            }
        )
    return items, semantic_used, len(merged)

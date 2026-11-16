"""ACL-scoped retrieval over the additive Persian RAG table."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.search.embeddings import EmbeddingUnavailable, get_rag_embedding_service
from app.search.text_normalizer import normalize_farsi
from app.models import DocumentChunkV2, DocumentRevision, SourceFile


@dataclass(slots=True)
class RAGHit:
    chunk: DocumentChunkV2
    filename: str
    score: float
    semantic: bool = False

    def as_source(self) -> dict[str, Any]:
        excerpt = self.chunk.text_original[:700]
        if len(self.chunk.text_original) > 700:
            excerpt += "…"
        return {
            "chunk_id": str(self.chunk.id),
            "file_id": str(self.chunk.file_id),
            "filename": self.filename,
            "page_no": self.chunk.page_no,
            "source": self.chunk.source,
            "excerpt": excerpt,
            "score": round(float(self.score), 6),
            "page_url": (
                f"/api/v1/files/source-files/{self.chunk.file_id}"
                + (f"#page={self.chunk.page_no}" if self.chunk.page_no else "")
            ),
        }


def _current_revision_clause():
    current_revision = exists(
        select(DocumentRevision.id).where(
            DocumentRevision.id == DocumentChunkV2.revision_id,
            DocumentRevision.is_current.is_(True),
        )
    )
    any_revision_for_file = exists(
        select(DocumentRevision.id).where(
            DocumentRevision.source_file_id == DocumentChunkV2.file_id,
        )
    )
    return or_(
        and_(DocumentChunkV2.revision_id.is_(None), ~any_revision_for_file),
        current_revision,
    )


def _clauses(
    *,
    project_ids: list[Any] | None,
    project_id: Any | None,
    file_ids: list[Any] | None,
):
    result = [DocumentChunkV2.is_active.is_(True), _current_revision_clause()]
    if project_id is not None:
        result.append(DocumentChunkV2.project_id == project_id)
    elif project_ids is not None:
        result.append(DocumentChunkV2.project_id.in_(project_ids) if project_ids else False)
    if file_ids is not None:
        result.append(DocumentChunkV2.file_id.in_(file_ids) if file_ids else False)
    return result


def _terms(value: str) -> list[str]:
    return [part for part in re.split(r"\s+", normalize_farsi(value)) if part]


def retrieve_rag_chunks(
    db: Session,
    question: str,
    *,
    project_ids: list[Any] | None = None,
    project_id: Any | None = None,
    file_ids: list[Any] | None = None,
    top_k: int = 8,
    min_score: float = 0.24,
) -> tuple[list[RAGHit], bool]:
    """Return top grounded chunks and whether semantic vectors were used.

    PostgreSQL uses normalized E5 query vectors and cosine distance.  SQLite and
    rows whose optional model is unavailable use a bounded normalized-text LIKE
    fallback so preview installations fail soft instead of loading a model.
    """

    normalized_question = normalize_farsi(question)
    if not normalized_question:
        return [], False
    limit = max(1, min(int(top_k), 50))
    clauses = _clauses(project_ids=project_ids, project_id=project_id, file_ids=file_ids)
    backend = db.get_bind().dialect.name
    hits: dict[Any, RAGHit] = {}
    semantic_used = False

    if backend == "postgresql":
        try:
            vector = get_rag_embedding_service().embed_query(normalized_question)
            distance = DocumentChunkV2.embedding.op("<=>")(vector)
            statement = (
                select(DocumentChunkV2, SourceFile.original_filename, distance.label("distance"))
                .join(SourceFile, SourceFile.id == DocumentChunkV2.file_id)
                .where(and_(*clauses), DocumentChunkV2.embedding.is_not(None))
                .order_by(distance.asc())
                .limit(max(limit * 4, 32))
            )
            for chunk, filename, raw_distance in db.execute(statement).all():
                score = 1.0 - float(raw_distance)
                if score >= min_score:
                    hits[chunk.id] = RAGHit(chunk=chunk, filename=filename, score=score, semantic=True)
            semantic_used = True
        except (EmbeddingUnavailable, ValueError, TypeError):
            semantic_used = False

    # Keyword rows supplement semantic results and are the complete SQLite path.
    term_clauses = [DocumentChunkV2.text_normalized.ilike(f"%{term}%") for term in _terms(normalized_question)]
    if term_clauses:
        keyword_statement = (
            select(DocumentChunkV2, SourceFile.original_filename)
            .join(SourceFile, SourceFile.id == DocumentChunkV2.file_id)
            .where(and_(*clauses), or_(*term_clauses))
            .order_by(DocumentChunkV2.created_at.desc())
            .limit(max(limit * 4, 32))
        )
        for chunk, filename in db.execute(keyword_statement).all():
            term_count = sum(
                1 for term in _terms(normalized_question) if term in (chunk.text_normalized or "")
            )
            keyword_score = term_count / max(1, len(_terms(normalized_question)))
            existing = hits.get(chunk.id)
            if existing is None:
                hits[chunk.id] = RAGHit(
                    chunk=chunk,
                    filename=filename,
                    score=keyword_score * 0.5,
                    semantic=False,
                )
            elif not existing.semantic:
                existing.score = max(existing.score, keyword_score * 0.5)

    ranked = sorted(hits.values(), key=lambda item: (-item.score, str(item.chunk.id)))
    return ranked[:limit], semantic_used


def retrieve_temporary_chunks(
    chunks: Iterable[Any],
    filename: str,
    question: str,
    *,
    top_k: int = 8,
    min_score: float = 0.24,
    use_semantic: bool = True,
) -> list[RAGHit]:
    """Rank non-persisted DOCX/PDF chunks for one request-scoped upload."""

    values = list(chunks)
    normalized_question = normalize_farsi(question)
    if not values or not normalized_question:
        return []
    hits: list[RAGHit] = []
    try:
        if not use_semantic:
            raise EmbeddingUnavailable("SQLite keyword fallback")
        service = get_rag_embedding_service()
        query_vector = service.embed_query(normalized_question)
        vectors = service.embed_passages([item.normalized_text for item in values])
        for item, vector in zip(values, vectors, strict=True):
            score = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            if score >= min_score:
                # The temporary object only needs the fields consumed by RAGHit;
                # it is never added to the database or exposed as an EDMS file.
                from app.models import DocumentChunkV2

                chunk = DocumentChunkV2(
                    id=uuid.uuid4(),
                    file_id=uuid.UUID(int=0),
                    project_id=uuid.UUID(int=0),
                    chunk_index=0,
                    page_no=item.page_no,
                    text_original=item.text,
                    text_normalized=item.normalized_text,
                    content_hash="",
                    source=item.source,
                    source_document=filename,
                    source_path=None,
                    embedding=vector,
                    embedding_model="intfloat/multilingual-e5-small",
                    embedding_dimensions=384,
                    is_active=True,
                )
                hits.append(RAGHit(chunk=chunk, filename=filename, score=float(score), semantic=True))
        return sorted(hits, key=lambda item: -item.score)[: max(1, min(top_k, 50))]
    except (EmbeddingUnavailable, ValueError, TypeError):
        terms = _terms(normalized_question)
        for item in values:
            count = sum(1 for term in terms if term in item.normalized_text)
            if count:
                from app.models import DocumentChunkV2

                chunk = DocumentChunkV2(
                    id=uuid.uuid4(),
                    file_id=uuid.UUID(int=0),
                    project_id=uuid.UUID(int=0),
                    chunk_index=0,
                    page_no=item.page_no,
                    text_original=item.text,
                    text_normalized=item.normalized_text,
                    content_hash="",
                    source=item.source,
                    source_document=filename,
                    source_path=None,
                    embedding=None,
                    embedding_model="intfloat/multilingual-e5-small",
                    embedding_dimensions=384,
                    is_active=True,
                )
                hits.append(RAGHit(chunk=chunk, filename=filename, score=count / max(1, len(terms)) * 0.5))
        return sorted(hits, key=lambda item: -item.score)[: max(1, min(top_k, 50))]


__all__ = ["RAGHit", "retrieve_rag_chunks", "retrieve_temporary_chunks"]

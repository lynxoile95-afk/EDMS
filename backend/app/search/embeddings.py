"""Lazy multilingual-E5 embedding adapters.

The legacy service remains a 768-dimensional ``multilingual-e5-base`` adapter.
The Persian file-QA service creates a separate 384-dimensional adapter, so no
existing vector or chunk is rewritten when the new feature is enabled.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence

from app.core.config import settings


class EmbeddingUnavailable(RuntimeError):
    """The optional sentence-transformers runtime/model is not available."""


class EmbeddingService:
    """A lazy, dimension-checked E5 adapter.

    ``dimensions`` and ``model_name`` are injectable for tests and for the
    additive Persian profile.  Existing callers that instantiate this class
    without arguments retain the old 768-dimensional contract.
    """

    dimensions = 768

    def __init__(
        self,
        model=None,
        *,
        dimensions: int | None = None,
        model_name: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._model = model
        self.dimensions = int(dimensions or self.dimensions)
        self.model_name = (model_name or settings.embedding_model).strip()
        self.batch_size = int(batch_size or settings.embedding_batch_size)
        self._lock = threading.Lock()
        self._encode_lock = threading.BoundedSemaphore(1)

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise EmbeddingUnavailable(
                    "sentence-transformers برای جست‌وجوی معنایی نصب نشده است."
                ) from exc
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    cache_folder=settings.embedding_cache_dir or None,
                    device="cpu",
                )
            except Exception as exc:  # noqa: BLE001 - do not expose model internals
                raise EmbeddingUnavailable(
                    "مدل embedding در این محیط قابل بارگذاری نیست."
                ) from exc
        return self._model

    def embed(self, texts: Sequence[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        prefixed = [f"{prefix}: {text}" for text in texts]
        try:
            with self._encode_lock:
                values = model.encode(
                    prefixed,
                    batch_size=max(1, min(self.batch_size, 64)),
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingUnavailable("تولید embedding ناموفق بود.") from exc
        rows = values.tolist() if hasattr(values, "tolist") else list(values)
        result = [[float(component) for component in row] for row in rows]
        if any(len(row) != self.dimensions for row in result):
            raise EmbeddingUnavailable(
                f"ابعاد embedding مدل با بردار {self.dimensions}بعدی سازگار نیست."
            )
        normalised: list[list[float]] = []
        for row in result:
            norm = math.sqrt(sum(component * component for component in row))
            if norm == 0:
                raise EmbeddingUnavailable("مدل embedding بردار صفر تولید کرد.")
            normalised.append([component / norm for component in row])
        return normalised

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts, prefix="passage")

    def embed_query(self, text: str) -> list[float]:
        rows = self.embed([text], prefix="query")
        return rows[0]


_service: EmbeddingService | None = None
_rag_service: EmbeddingService | None = None
_service_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    """Return the unchanged legacy 768-dimensional service."""

    global _service
    with _service_lock:
        if _service is None:
            _service = EmbeddingService(
                dimensions=768,
                model_name=settings.embedding_model,
                batch_size=settings.embedding_batch_size,
            )
        return _service


def get_rag_embedding_service() -> EmbeddingService:
    """Return the lazy 384-dimensional multilingual-e5-small service."""

    global _rag_service
    with _service_lock:
        if _rag_service is None:
            _rag_service = EmbeddingService(
                dimensions=384,
                model_name=settings.file_qa_embedding_model,
                # The reference RAG contract uses batch size 16 exactly.
                batch_size=16,
            )
        return _rag_service


def reset_embedding_service() -> None:
    """Test hook; production keeps one lazy model per worker/profile."""

    global _service, _rag_service
    with _service_lock:
        _service = None
        _rag_service = None


__all__ = [
    "EmbeddingUnavailable",
    "EmbeddingService",
    "get_embedding_service",
    "get_rag_embedding_service",
    "reset_embedding_service",
]

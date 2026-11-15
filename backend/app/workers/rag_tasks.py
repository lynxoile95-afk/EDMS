"""Celery entry point for the optional Persian-RAG index queue."""

from __future__ import annotations

import threading

from app.core.config import settings
from app.search.rag_index import run_rag_index_job
from app.workers.celery_app import celery_app

_RAG_SEMAPHORE = threading.BoundedSemaphore(max(1, int(settings.index_max_concurrent_jobs)))


@celery_app.task(name="edms.search.index_rag_file", bind=True, max_retries=1)
def index_rag_file(self, job_id: str) -> dict:
    with _RAG_SEMAPHORE:
        return run_rag_index_job(job_id)

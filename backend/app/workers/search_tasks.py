"""Celery entry points for the isolated, opt-in search queue."""

from __future__ import annotations

import threading

from app.core.config import settings
from app.search.indexing import run_index_job
from app.workers.celery_app import celery_app


_INDEX_SEMAPHORE = threading.BoundedSemaphore(max(1, int(settings.index_max_concurrent_jobs)))


@celery_app.task(name="edms.search.index_file", bind=True, max_retries=1)
def index_file(self, job_id: str) -> dict:
    # The service owns idempotency and page-boundary commits. Keeping this task
    # small also makes it straightforward to run from admin backfill.
    with _INDEX_SEMAPHORE:
        return run_index_job(job_id)

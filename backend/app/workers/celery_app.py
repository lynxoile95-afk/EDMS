"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "edms",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,                 # at-least-once; tasks are idempotent
    worker_prefetch_multiplier=1,        # long extraction tasks block the prefetch window
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_task_always_eager,
    task_routes={
        "edms.ingest.*": {"queue": "ingest"},
        "edms.extract.*": {"queue": "extract"},
        "edms.transmittal.*": {"queue": "transmittal"},
        "edms.notify.*": {"queue": "notify"},
    },
    task_default_queue="ingest",
    task_annotations={
        "edms.extract.process_session": {"rate_limit": None, "max_retries": 2},
        "edms.notify.deliver": {"max_retries": 5},
    },
    beat_schedule={
        "deliver-notifications": {
            "task": "edms.notify.deliver",
            "schedule": 60.0,  # every minute
        },
        "due-reminders": {
            "task": "edms.notify.reminders",
            "schedule": crontab(hour=6, minute=0),  # daily 06:00 UTC
        },
        "cleanup-temp": {
            "task": "edms.maintenance.cleanup",
            "schedule": crontab(hour=2, minute=30),
        },
    },
)

"""
app/enqueue_task.py

Thin Celery task wrapper around app.queue.enqueue_pending_candidates,
registered with Celery Beat in app/celery_app.py.

Kept separate from app/queue.py so the enqueue logic itself has no
Celery import/dependency and can be run standalone (e.g. via a
management script or right after /sync-sheets) without needing a full
Celery worker running.
"""

from app.celery_app import celery_app
from app.queue import enqueue_pending_candidates


@celery_app.task(name="app.enqueue_task.enqueue_pending_candidates_task")
def enqueue_pending_candidates_task():
    enqueue_pending_candidates()
    return "Enqueued pending candidates"
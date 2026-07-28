"""
app/retry_no_whatsapp_task.py

Thin Celery task wrapper around app.queue.retry_recoverable_no_whatsapp_candidates,
registered with Celery Beat in app/celery_app.py to run automatically
once a day. This means candidates blocked by a recoverable Meta-side
error (e.g. 63049, tied to Business Verification status) get retried
on their own -- no one has to remember to run the script manually.

Kept separate from app/queue.py for the same reason as app/enqueue_task.py:
the retry logic itself has no Celery dependency and can run standalone.
"""
from app.celery_app import celery_app
from app.queue import retry_recoverable_no_whatsapp_candidates


@celery_app.task(name="app.retry_no_whatsapp_task.retry_recoverable_no_whatsapp_task")
def retry_recoverable_no_whatsapp_task():
    count = retry_recoverable_no_whatsapp_candidates()
    return f"Retried {count} recoverable no_whatsapp candidate(s)"
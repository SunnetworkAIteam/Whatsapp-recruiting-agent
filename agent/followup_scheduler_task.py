"""
agent/followup_scheduler_task.py

Thin Celery task wrapper around agent.followup_scheduler.run_followup_cycle,
registered with Celery Beat in app/celery_app.py.

Kept separate from followup_scheduler.py so that the scheduling logic
itself has no Celery import/dependency and can be unit tested or run
standalone (e.g. via a management script) without a Celery worker.
"""

from app.celery_app import celery_app
from agent.followup_scheduler import run_followup_cycle


@celery_app.task(name="agent.followup_scheduler_task.run_followup_cycle_task")
def run_followup_cycle_task() -> dict:
    return run_followup_cycle()
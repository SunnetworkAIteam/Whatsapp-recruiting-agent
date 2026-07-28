import os
from celery import Celery
from celery.schedules import crontab

# Read Redis connection from environment
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "candidate_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["agent.followup_scheduler_task", "app.tasks", "app.enqueue_task", "app.retry_no_whatsapp_task"],
)

# Rate limit: max 10 tasks per minute
celery_app.conf.task_annotations = {
    "app.tasks.send_candidate": {"rate_limit": "10/m"}
}

# Beat schedule: run the follow-up cycle periodically. The task itself
# checks whether it's actually within the 9 PM IST send window and is a
# no-op outside it, so it's safe to run this more frequently than the
# window itself -- e.g. every 15 minutes -- to catch the window promptly
# without needing exact crontab alignment to IST (Celery Beat crontab
# times below are in UTC by default).
#
# 9 PM IST == 3:30 PM UTC. Running every 15 min from 3:30 PM to 11:45 PM
# UTC covers the whole "after 9 PM IST" window for same-day sends.
celery_app.conf.beat_schedule = {
    # No time-of-day restriction anymore (per project decision) --
    # both the initial-outreach enqueue and the follow-up cycle run
    # purely on elapsed time relative to each candidate's own timestamps,
    # so it's safe (and desired, for fast testing/turnaround) to check
    # frequently.
    "enqueue-pending-candidates": {
        "task": "app.enqueue_task.enqueue_pending_candidates_task",
        "schedule": crontab(minute="*/5"),
    },
    "run-followup-cycle": {
        "task": "agent.followup_scheduler_task.run_followup_cycle_task",
        "schedule": crontab(minute="*/5"),
    },
    # Real problem this solves: candidates blocked by a recoverable
    # Meta-side error (e.g. 63049, tied to Business Verification status
    # still being in progress) previously stayed stuck until someone
    # remembered to manually run scripts/retry_recoverable_no_whatsapp.py.
    # Running this once a day means recovery happens automatically, with
    # no manual intervention needed.
    "retry-recoverable-no-whatsapp": {
        "task": "app.retry_no_whatsapp_task.retry_recoverable_no_whatsapp_task",
        "schedule": crontab(hour=6, minute=0),  # once daily, 6 AM UTC (11:30 AM IST)
    },
}
celery_app.conf.timezone = "UTC"
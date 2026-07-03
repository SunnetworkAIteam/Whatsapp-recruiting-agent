import os
from celery import Celery

# Read Redis connection from environment
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "candidate_worker",
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Rate limit: max 10 tasks per minute
celery_app.conf.task_annotations = {
    "app.tasks.send_candidate": {"rate_limit": "10/m"}
}

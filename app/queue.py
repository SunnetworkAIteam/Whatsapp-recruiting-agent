from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

# Spacing between each candidate's outreach send. Real reason this
# exists: sending every pending candidate's message at once looked and
# felt like a mass blast, and there's no technical need to send them
# all simultaneously -- Twilio/Meta's actual rate limit (80 msgs/sec)
# is far higher than anything this project will ever hit. A small,
# deliberate gap between sends is simply better practice.
SEND_STAGGER_SECONDS = 2


def enqueue_pending_candidates():
    global supabase
    if supabase is None:
        raise RuntimeError("Supabase not configured.")
    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
    for i, row in enumerate(result.data or []):
        # countdown, not .delay() -- schedules each send with an
        # increasing delay (0s, 2s, 4s, ...) so they fire one by one,
        # spaced out, rather than all at once. This doesn't block this
        # function itself; Celery just holds each task until its
        # countdown elapses.
        send_candidate.apply_async(args=[row["id"]], countdown=i * SEND_STAGGER_SECONDS)

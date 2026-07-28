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


# Error codes considered recoverable for the retry logic below --
# 63024 (genuinely not a WhatsApp number) is deliberately excluded,
# since retrying it can never succeed.
RECOVERABLE_NO_WHATSAPP_ERROR_CODES = ["63049", "63032"]


def retry_recoverable_no_whatsapp_candidates() -> int:
    """
    Resets any candidate flagged "no_whatsapp" with a recoverable error
    code (Meta-side throttling, not a genuinely invalid number) back to
    "pending", then enqueues them with the normal staggered send.

    Shared by both scripts/retry_recoverable_no_whatsapp.py (manual,
    on-demand use) and app.retry_no_whatsapp_task (automatic daily
    Celery Beat run) -- kept here, not duplicated in each, so both
    always stay in sync.

    Returns the number of candidates reset and retried.
    """
    global supabase
    if supabase is None:
        raise RuntimeError("Supabase not configured.")

    result = (
        supabase.table("candidates")
        .select("id")
        .eq("platform", "no_whatsapp")
        .in_("delivery_error_code", RECOVERABLE_NO_WHATSAPP_ERROR_CODES)
        .execute()
    )
    candidates = result.data or []
    if not candidates:
        return 0

    for r in candidates:
        supabase.table("candidates").update({
            "status": "pending",
            "platform": "whatsapp",
            "delivery_error_code": None,
            "delivery_error_message": None,
            "delivery_status": None,
        }).eq("id", r["id"]).execute()

    enqueue_pending_candidates()
    return len(candidates)


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

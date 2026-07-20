"""
app/tasks.py

Celery task that sends the initial JOB_OUTREACH WhatsApp template to a
single pending candidate. Triggered via app/queue.py's
enqueue_pending_candidates(), which is itself called periodically by
Celery Beat (see app/celery_app.py) so newly Sheets-synced candidates get
messaged automatically without manual intervention.

Per project decision: the first outreach message has NO time-of-day
restriction (unlike follow-ups, which are purely relative to when the
first message was sent -- see agent/followup_scheduler.py). This can
fire any time of day.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

from app.celery_app import celery_app
from app.phone_utils import to_e164_india
from agent.pipeline import _build_job_outreach_variables
from agent.send_functions import send_template_message, MessagingConfigError, MessageSendError

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

JOB_OUTREACH_TEMPLATE_SID = os.environ.get("TWILIO_JOB_OUTREACH_TEMPLATE_SID")


@celery_app.task(bind=True, max_retries=3)
def send_candidate(self, candidate_id: str):
    if supabase is None:
        raise RuntimeError("Supabase client not initialized (check SUPABASE_URL/SUPABASE_KEY).")

    result = (
        supabase.table("candidates")
        .select("*")
        .eq("id", candidate_id)
        .eq("status", "pending")
        .execute()
    )
    if not result.data:
        return f"Candidate {candidate_id} not pending (already sent or not found)"

    candidate = result.data[0]

    if not JOB_OUTREACH_TEMPLATE_SID:
        return f"Candidate {candidate_id} not sent: TWILIO_JOB_OUTREACH_TEMPLATE_SID not configured"

    template_variables = _build_job_outreach_variables(candidate)

    status_callback_url = os.environ.get("TWILIO_STATUS_CALLBACK_URL")

    try:
        twilio_sid = send_template_message(
            to=to_e164_india(candidate["phone"]),
            template_sid=JOB_OUTREACH_TEMPLATE_SID,
            template_variables=template_variables,
            message_type="INITIAL_OUTREACH",
            status_callback=status_callback_url,
        )
    except (MessagingConfigError, MessageSendError) as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    now_ts = datetime.now(timezone.utc).isoformat()

    # Reconstruct the actual approved template text (this is the exact
    # candidate_outreach_utility body -- see the Utility template
    # submitted to Meta) with real variables filled in, so this send
    # shows up in the dashboard's Messages tab even before the candidate
    # ever replies. Previously, only LLM-generated replies got added to
    # conversation_history, so anyone who'd only received outreach but
    # hadn't replied yet was invisible there.
    sent_text = (
        f"Hi {template_variables['1']}, this is Nisha from Amerisource Solutions Pvt. Ltd. "
        f"regarding your application status for the {template_variables['2']} role. "
        f"This is a {template_variables['3']} position with {template_variables['4']} working hours. "
        f"Please reply to this message to continue with your application. Thank you!"
    )
    history = (candidate.get("conversation_history") or []) + [{"role": "assistant", "content": sent_text}]

    supabase.table("candidates").update({
        "status": "messaged",
        "first_outreach_sent_at": now_ts,
        "last_template_sent_at": now_ts,
        "conversation_history": history,
    }).eq("id", candidate_id).execute()

    return f"Candidate {candidate_id} sent successfully (twilio_sid={twilio_sid})"
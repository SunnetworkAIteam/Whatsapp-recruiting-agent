"""
agent/followup_scheduler.py

Periodic (Celery Beat) task that sends WhatsApp follow-up templates to
candidates in two separate tracks:

Track A -- never replied to the initial outreach:
    outreach (already sent) -> 24h, no reply -> Template 2 (NO_REPLY)
                             -> 24h, still no reply -> Template 4 (FINAL_REMINDER)

Track B -- replied and had a conversation, but went silent before
scheduling an interview:
    24h after their last message, if status is "replied" but hasn't
    reached "qualified"/"awaiting_interview"/"booked" -> Template 3
    (INTERVIEW_FOLLOW_UP)

Both tracks are purely time-based off elapsed hours since the relevant
prior message -- there is no time-of-day restriction (per project
decision, only the initial outreach and follow-up timing matters, not
when in the day it happens).

Track B's condition (status="replied" + 24h of inactivity) is a stand-in
until a dedicated DB column/signal for "had a conversation but didn't
schedule" is added -- see project notes. Swap the condition in
_is_track_b_eligible() once that column exists.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from app.status import get_candidates_pending_followup, update_candidate_metadata, get_safe_display_name
from app.phone_utils import to_e164_india
from agent.send_functions import send_template_message, MessagingConfigError, MessageSendError

logger = logging.getLogger("agent.followup_scheduler")

# Spacing for both tracks' timing checks.
FOLLOWUP_INTERVAL = timedelta(hours=24)

# Track A template SIDs
NO_REPLY_TEMPLATE_SID: Optional[str] = os.environ.get("TWILIO_NO_REPLY_TEMPLATE_SID")
FINAL_REMINDER_TEMPLATE_SID: Optional[str] = os.environ.get("TWILIO_FINAL_REMINDER_TEMPLATE_SID")

# Track B template SID
INTERVIEW_FOLLOW_UP_TEMPLATE_SID: Optional[str] = os.environ.get("TWILIO_INTERVIEW_FOLLOW_UP_TEMPLATE_SID")

# Public URL Twilio calls with delivery status updates (failed/undelivered
# etc). Used to detect candidates who couldn't be reached via WhatsApp --
# see webhook/app.py's /webhooks/whatsapp/status endpoint.
STATUS_CALLBACK_URL: Optional[str] = os.environ.get("TWILIO_STATUS_CALLBACK_URL")

# Statuses that mean a candidate has already moved past "just replied,
# hasn't scheduled" -- Track B should not fire for these.
_SCHEDULED_OR_BEYOND_STATUSES = {"qualified", "awaiting_interview", "booked"}


def _parse_timestamp(value) -> Optional[datetime]:
    """Parse a Supabase-returned timestamp (str or None) into an aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _build_followup_variables(candidate: dict) -> dict:
    """
    All three follow-up templates (NO_REPLY, FINAL_REMINDER,
    INTERVIEW_FOLLOW_UP) use the same two placeholders:
        {{1}} = candidate name
        {{2}} = role
    Without passing these, Twilio silently falls back to the sample
    values entered at template-approval time (e.g. "Rajesh Kumar",
    "AI Quality & Optimization Engineer") instead of the real candidate's
    data -- this bit us once already on a live test send.
    """
    return {
        "1": get_safe_display_name(candidate),
        "2": candidate.get("role") or "this role",
    }


# ---------------------------------------------------------------------------
# Track A: never replied
# ---------------------------------------------------------------------------

def _is_track_a_candidate(candidate: dict) -> bool:
    return candidate.get("status") in ("messaged", "follow_up_sent")


def _due_for_no_reply_template(candidate: dict, now_utc: datetime) -> bool:
    if candidate.get("followup_1_sent_at"):
        return False  # already sent
    first_sent = _parse_timestamp(candidate.get("first_outreach_sent_at"))
    if not first_sent:
        return False  # never actually messaged yet, nothing to follow up on
    return now_utc - first_sent >= FOLLOWUP_INTERVAL


def _due_for_final_reminder(candidate: dict, now_utc: datetime) -> bool:
    if candidate.get("followup_2_sent_at"):
        return False  # already sent
    no_reply_sent = _parse_timestamp(candidate.get("followup_1_sent_at"))
    if not no_reply_sent:
        return False  # hasn't had the no-reply template yet
    return now_utc - no_reply_sent >= FOLLOWUP_INTERVAL


# ---------------------------------------------------------------------------
# Track B: replied but didn't schedule
# ---------------------------------------------------------------------------

def _is_track_b_eligible(candidate: dict, now_utc: datetime) -> bool:
    """
    True if this candidate replied and had a conversation, but hasn't
    reached scheduling/qualified/booked, and it's been 24h+ since their
    last message with no further activity.

    NOTE: this is a stand-in condition pending a dedicated DB column for
    "had a conversation but didn't schedule" (see project notes). Update
    this function once that column exists instead of inferring from
    status + timing.
    """
    if candidate.get("status") != "replied":
        return False
    if candidate.get("interview_followup_sent_at"):
        return False  # already sent, don't repeat

    last_msg = _parse_timestamp(candidate.get("last_candidate_message_at"))
    if not last_msg:
        return False
    return now_utc - last_msg >= FOLLOWUP_INTERVAL


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _record_followup_sent(candidate, candidate_id, timestamp_field, now_utc, label, name, role):
    """
    Persists the followup timestamp AND appends a clearly-labeled entry
    to conversation_history, so this send shows up in the dashboard's
    Messages tab even before the candidate replies. The exact approved
    wording of these templates isn't reproduced here (only the initial
    JOB_OUTREACH template's exact text is known/reconstructed, in
    app/tasks.py) -- labeling honestly as "[<label> sent]" avoids
    presenting fabricated text as if it were the real message.
    """
    label_text = f"[{label} sent to {name or 'candidate'} for {role or 'this role'}]"
    history = (candidate.get("conversation_history") or []) + [{"role": "assistant", "content": label_text}]
    update_candidate_metadata(candidate_id, {timestamp_field: now_utc, "conversation_history": history})


def run_followup_cycle() -> dict:
    """
    Main entry point, intended to be invoked by a Celery Beat schedule
    (see app/celery_app.py). Evaluates every eligible candidate across
    both tracks and sends the appropriate template if due.

    Returns a summary dict for logging/observability.
    """
    now_utc = datetime.now(timezone.utc)

    missing_sids = [
        name for name, sid in (
            ("TWILIO_NO_REPLY_TEMPLATE_SID", NO_REPLY_TEMPLATE_SID),
            ("TWILIO_FINAL_REMINDER_TEMPLATE_SID", FINAL_REMINDER_TEMPLATE_SID),
            ("TWILIO_INTERVIEW_FOLLOW_UP_TEMPLATE_SID", INTERVIEW_FOLLOW_UP_TEMPLATE_SID),
        ) if not sid
    ]
    if missing_sids:
        logger.error(
            "Missing follow-up template SID configuration: %s; skipping this cycle.",
            missing_sids,
        )
        return {"sent": 0, "error": f"Missing template SID configuration: {missing_sids}"}

    candidates = get_candidates_pending_followup()
    sent_no_reply, sent_final_reminder, sent_interview_followup, errors = 0, 0, 0, []

    for candidate in candidates:
        candidate_id = candidate["id"]
        phone = to_e164_india(candidate["phone"])

        try:
            if _is_track_a_candidate(candidate):
                if _due_for_no_reply_template(candidate, now_utc):
                    twilio_sid = send_template_message(
                        to=phone,
                        template_sid=NO_REPLY_TEMPLATE_SID,
                        template_variables=_build_followup_variables(candidate),
                        message_type="TRACK_A_NO_REPLY",
                        status_callback=STATUS_CALLBACK_URL,
                    )
                    _record_followup_sent(candidate, candidate_id, "followup_1_sent_at", now_utc, "No-reply follow-up", candidate.get("name"), candidate.get("role"))
                    logger.info(
                        "Track A: sent no-reply template to candidate %s (twilio_sid=%s)",
                        candidate_id, twilio_sid,
                    )
                    sent_no_reply += 1
                    continue

                if _due_for_final_reminder(candidate, now_utc):
                    twilio_sid = send_template_message(
                        to=phone,
                        template_sid=FINAL_REMINDER_TEMPLATE_SID,
                        template_variables=_build_followup_variables(candidate),
                        message_type="TRACK_A_FINAL_REMINDER",
                        status_callback=STATUS_CALLBACK_URL,
                    )
                    _record_followup_sent(candidate, candidate_id, "followup_2_sent_at", now_utc, "Final reminder", candidate.get("name"), candidate.get("role"))
                    logger.info(
                        "Track A: sent final reminder to candidate %s (twilio_sid=%s)",
                        candidate_id, twilio_sid,
                    )
                    sent_final_reminder += 1
                    continue

            elif _is_track_b_eligible(candidate, now_utc):
                twilio_sid = send_template_message(
                    to=phone,
                    template_sid=INTERVIEW_FOLLOW_UP_TEMPLATE_SID,
                    template_variables=_build_followup_variables(candidate),
                    message_type="TRACK_B_INTERVIEW_FOLLOW_UP",
                    status_callback=STATUS_CALLBACK_URL,
                )
                _record_followup_sent(candidate, candidate_id, "interview_followup_sent_at", now_utc, "Interview follow-up", candidate.get("name"), candidate.get("role"))
                logger.info(
                    "Track B: sent interview follow-up to candidate %s (twilio_sid=%s)",
                    candidate_id, twilio_sid,
                )
                sent_interview_followup += 1

        except (MessagingConfigError, MessageSendError) as exc:
            logger.error(
                "Failed to send follow-up to candidate %s: %s",
                candidate_id, exc, exc_info=True,
            )
            errors.append({"candidate_id": candidate_id, "error": str(exc)})

    summary = {
        "sent_no_reply": sent_no_reply,
        "sent_final_reminder": sent_final_reminder,
        "sent_interview_followup": sent_interview_followup,
        "errors": errors,
        "evaluated": len(candidates),
    }
    logger.info("Follow-up cycle complete: %s", summary)
    return summary
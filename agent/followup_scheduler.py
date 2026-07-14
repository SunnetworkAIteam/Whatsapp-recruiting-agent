"""
agent/followup_scheduler.py

Periodic (Celery Beat) task that sends WhatsApp follow-up templates to
candidates who haven't replied after the initial outreach.

Business rules (per project notes):
    * Two follow-ups total, 24 hours apart.
    * All outbound messages (first + follow-ups) are sent after 9 PM IST only.
    * Each follow-up is a different, escalating-in-tone template.

This module only decides WHO is due for a follow-up and WHEN -- actual
sending is delegated to messaging.send_template_message, and template
selection SIDs are read the same way agent/pipeline.py does.

This intentionally does NOT change candidate `status` (which stays
"messaged" throughout both follow-ups) to avoid the status/transition
mismatch currently present in agent/pipeline.py for post-outreach states
(see notes below). Follow-up progress is tracked purely via the
followup_1_sent_at / followup_2_sent_at columns already in the schema.

Known issue this module deliberately avoids replicating:
    agent/pipeline.py transitions candidates to statuses such as
    "follow_up_sent" / "final_reminder_sent" / "awaiting_interview", none
    of which exist in app/status.py's VALID_STATUSES or
    ALLOWED_TRANSITIONS. That will raise ValueError at runtime whenever
    those paths are hit. This is a pre-existing bug (from the Twilio
    send functions PR) independent of this scheduler and should be fixed
    separately in app/status.py and/or agent/pipeline.py.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.status import get_candidates_pending_followup, update_candidate_metadata
from agent.send_functions import send_template_message, MessagingConfigError, MessageSendError

logger = logging.getLogger("agent.followup_scheduler")

IST = ZoneInfo("Asia/Kolkata")

# Only send outbound messages after this hour, IST (24h clock).
SEND_AFTER_HOUR_IST = 21  # 9 PM

# Spacing between outreach -> follow-up 1 -> follow-up 2.
FOLLOWUP_INTERVAL = timedelta(hours=24)

FOLLOWUP_1_TEMPLATE_SID: Optional[str] = os.environ.get("TWILIO_NO_REPLY_TEMPLATE_SID")
FOLLOWUP_2_TEMPLATE_SID: Optional[str] = os.environ.get("TWILIO_FINAL_REMINDER_TEMPLATE_SID")


def _is_within_send_window(now_ist: datetime) -> bool:
    """
    True if it's currently at/after 9 PM IST (and before midnight -- the
    project notes only specify "after 9 PM", so this treats the window as
    open from 9 PM until end of day; adjust here if a specific end time is
    later decided).
    """
    return now_ist.hour >= SEND_AFTER_HOUR_IST


def _parse_timestamp(value) -> Optional[datetime]:
    """Parse a Supabase-returned timestamp (str or None) into an aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _due_for_followup_1(candidate: dict, now_utc: datetime) -> bool:
    if candidate.get("followup_1_sent_at"):
        return False  # already sent
    first_sent = _parse_timestamp(candidate.get("first_outreach_sent_at"))
    if not first_sent:
        return False  # never actually messaged yet, nothing to follow up on
    return now_utc - first_sent >= FOLLOWUP_INTERVAL


def _due_for_followup_2(candidate: dict, now_utc: datetime) -> bool:
    if candidate.get("followup_2_sent_at"):
        return False  # already sent
    followup_1_sent = _parse_timestamp(candidate.get("followup_1_sent_at"))
    if not followup_1_sent:
        return False  # hasn't had followup 1 yet
    return now_utc - followup_1_sent >= FOLLOWUP_INTERVAL


def run_followup_cycle() -> dict:
    """
    Main entry point, intended to be invoked by a Celery Beat schedule
    (see app/celery_app.py). Evaluates every 'messaged' candidate and
    sends the appropriate follow-up template if due and within the
    9 PM IST send window.

    Returns a summary dict for logging/observability.
    """
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)

    if not _is_within_send_window(now_ist):
        logger.info(
            "Outside the 9 PM IST send window (now=%s IST); skipping this cycle.",
            now_ist.isoformat(),
        )
        return {"sent": 0, "skipped_outside_window": True}

    if not FOLLOWUP_1_TEMPLATE_SID or not FOLLOWUP_2_TEMPLATE_SID:
        logger.error(
            "Follow-up template SIDs are not configured "
            "(TWILIO_NO_REPLY_TEMPLATE_SID / TWILIO_FINAL_REMINDER_TEMPLATE_SID); "
            "skipping this cycle."
        )
        return {"sent": 0, "error": "Missing template SID configuration"}

    candidates = get_candidates_pending_followup()
    sent_followup_1, sent_followup_2, errors = 0, 0, []

    for candidate in candidates:
        candidate_id = candidate["id"]
        phone = candidate["phone"]

        try:
            if _due_for_followup_1(candidate, now_utc):
                twilio_sid = send_template_message(
                    to=phone,
                    template_sid=FOLLOWUP_1_TEMPLATE_SID,
                    message_type="NO_REPLY_FOLLOWUP_1",
                )
                update_candidate_metadata(candidate_id, {"followup_1_sent_at": now_utc})
                logger.info(
                    "Sent follow-up 1 to candidate %s (twilio_sid=%s)",
                    candidate_id, twilio_sid,
                )
                sent_followup_1 += 1
                continue  # don't also evaluate followup_2 in the same cycle

            if _due_for_followup_2(candidate, now_utc):
                twilio_sid = send_template_message(
                    to=phone,
                    template_sid=FOLLOWUP_2_TEMPLATE_SID,
                    message_type="FINAL_REMINDER_FOLLOWUP_2",
                )
                update_candidate_metadata(candidate_id, {"followup_2_sent_at": now_utc})
                logger.info(
                    "Sent follow-up 2 (final reminder) to candidate %s (twilio_sid=%s)",
                    candidate_id, twilio_sid,
                )
                sent_followup_2 += 1

        except (MessagingConfigError, MessageSendError) as exc:
            logger.error(
                "Failed to send follow-up to candidate %s: %s",
                candidate_id, exc, exc_info=True,
            )
            errors.append({"candidate_id": candidate_id, "error": str(exc)})

    summary = {
        "sent_followup_1": sent_followup_1,
        "sent_followup_2": sent_followup_2,
        "errors": errors,
        "evaluated": len(candidates),
    }
    logger.info("Follow-up cycle complete: %s", summary)
    return summary
"""
agent/pipeline.py

Implements handle_candidate_reply -- the function webhook/orchestrator.py's
set_message_processor() is wired up to via:

    from webhook.orchestrator import set_message_processor
    from agent.pipeline import handle_candidate_reply
    set_message_processor(handle_candidate_reply)

(see README.md, "Integrating the agent module")

Flow: webhook receives a reply -> build candidate snapshot state -> record inbound timestamp 
      -> rule_engine.gate_message() -> Route based on action (BLOCK_OPT_OUT, ESCALATE_TO_HUMAN, 
         NO_ACTION, SEND_TEMPLATE, or PASS_TO_LLM) -> Record outcome/metadata in Supabase.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from agent.llm_layer import CandidateStatus, process_message
from agent.rule_engine import CandidateState, RuleAction, TemplateType, gate_message

# Import webhook schema
from webhook.schemas import TwilioInboundMessage

# Database operations and status utility functions
from app.status import (
    get_candidate_by_phone, 
    record_inbound_message, 
    transition_status,
    update_candidate_metadata,  # Assumed implemented in app/status.py to handle metadata fields
    get_role_work_details,
    record_unmatched_message,
    create_candidate_for_unknown_number,
    get_safe_display_name,
)
from app.validation import normalize_phone

# Messaging layers and specific messaging exceptions
from agent.send_functions import (
    send_template_message, 
    send_text_message,  # Assumed exported from messaging.py (formerly send_free_form_message)
    MessagingConfigError,
    MessageSendError
)

logger = logging.getLogger("agent.pipeline")


def _parse_ts(value):
    """
    Parse a Supabase-returned timestamp into an aware datetime.
    Supabase always returns timestamptz columns as ISO strings, not
    datetime objects -- but CandidateState/rule_engine.py's hours_since()
    needs real datetimes to do `now - past_time` arithmetic.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

# ---------------------------------------------------------------------------
# Template Type to Twilio Content SID Mapping
# ---------------------------------------------------------------------------
TEMPLATE_SID_MAPPING = {
    TemplateType.JOB_OUTREACH: os.environ.get("TWILIO_JOB_OUTREACH_TEMPLATE_SID"),
    TemplateType.NO_REPLY: os.environ.get("TWILIO_NO_REPLY_TEMPLATE_SID"),
    TemplateType.FINAL_REMINDER: os.environ.get("TWILIO_FINAL_REMINDER_TEMPLATE_SID"),
    TemplateType.INTERVIEW_FOLLOW_UP: os.environ.get("TWILIO_INTERVIEW_FOLLOW_UP_TEMPLATE_SID"),
}

# Public URL Twilio calls with delivery status updates (failed/undelivered
# etc). Used to detect candidates who couldn't be reached via WhatsApp --
# see webhook/app.py's /webhooks/whatsapp/status endpoint.
STATUS_CALLBACK_URL = os.environ.get("TWILIO_STATUS_CALLBACK_URL")

# ---------------------------------------------------------------------------
# Role -> work arrangement details, used to fill the JOB_OUTREACH template's
# {{3}} (work type) and {{4}} (shift/hours) placeholders.
#
# TODO: values below are placeholders (TBD) pending confirmation from HR.
# Fill in the real work_type ("Remote" / "Onsite" / "Hybrid") and hours for
# each role before sending real outreach. Sheet/role names must match
# exactly what comes from Google Sheets ingestion (see app/google_sheets.py
# -- each sheet name becomes the candidate's `role`).
# ---------------------------------------------------------------------------
# NOTE: role work details now live in the role_work_details DB table,
# editable by HR via the dashboard's Settings tab -- see
# app/status.py's get_role_work_details(). The hardcoded dict that used
# to be here required an engineer to edit code every time HR needed to
# change a role's work type/hours; that's gone now.




def _build_job_outreach_variables(candidate: dict) -> dict:
    """
    Build the {{1}}-{{4}} content_variables for the JOB_OUTREACH template:
        {{1}} = candidate name
        {{2}} = role
        {{3}} = work type (Remote/Onsite/Hybrid)
        {{4}} = shift/working hours

    Falls back to "TBD" for any role not yet configured in the
    role_work_details table -- logs a warning so this doesn't silently
    ship "TBD" to a real candidate without visibility.
    """
    role = candidate.get("role") or ""
    details = get_role_work_details(role)
    if details["work_type"] == "TBD" and details["hours"] == "TBD":
        logger.warning(
            "No role_work_details configured for role '%s'; using placeholder "
            "values. HR can fill this in via the dashboard's Settings tab.",
            role,
        )

    return {
        "1": get_safe_display_name(candidate),
        "2": role or "this role",
        "3": details["work_type"],
        "4": details["hours"],
    }


def handle_candidate_reply(message: TwilioInboundMessage) -> dict:
    """
    Orchestrates a single inbound candidate reply end-to-end.

    Returns a dict describing the outcome, as required by
    webhook/orchestrator.py's `process_inbound_message`.
    """
    # Twilio sends inbound numbers as "whatsapp:+91XXXXXXXXXX", but
    # candidates are stored with normalized bare-digit phone numbers (see
    # app/validation.py's normalize_phone, used during Sheets ingestion).
    # Without normalizing here, real candidates who reply would never be
    # found -- get_candidate_by_phone() would look for the raw
    # "whatsapp:+91..." string, which never matches what's stored.
    normalized_from = normalize_phone(message.from_number)
    candidate = get_candidate_by_phone(normalized_from)

    if candidate is None:
        logger.info(
            "No candidate found for phone=%s (sid=%s); auto-creating a record "
            "so this conversation is treated normally.",
            message.from_number,
            message.message_sid,
        )
        # Keep a permanent audit log of this...
        record_unmatched_message(message.from_number, message.body)
        # ...but ALSO create a real candidate record and let this message
        # flow through the exact same pipeline as any known candidate --
        # so it shows up as a normal conversation in the dashboard's
        # Messages tab, and the agent can actually reply, rather than
        # this being a dead end with no response.
        candidate = create_candidate_for_unknown_number(normalized_from)
    candidate_id = candidate["id"]
    phone_number = message.from_number

    # ---------------------------------------------------------------------------
    # Timing Guardrail: Construct state snapshot BEFORE updating inbound timestamp
    # ---------------------------------------------------------------------------
    state = CandidateState(
        first_outreach_sent_at=_parse_ts(candidate.get("first_outreach_sent_at")),
        last_candidate_message_at=_parse_ts(candidate.get("last_candidate_message_at")),
        last_template_sent_at=_parse_ts(candidate.get("last_template_sent_at")),
        interview_scheduled=candidate.get("interview_scheduled", False),
        no_reply_template_sent=candidate.get("no_reply_template_sent", False),
        final_reminder_sent=candidate.get("final_reminder_sent", False)
    )

    # Now safe to record the inbound message event in the database
    record_inbound_message(candidate_id)

    # Global escalation check -- BEFORE the rule engine runs at all. The
    # rule engine (agent/rule_engine.py) makes its decisions purely from
    # CandidateState (interview_scheduled, first_outreach_sent_at, etc.)
    # and has no awareness of the "escalated" status itself, so without
    # this check here it could still decide to SEND_TEMPLATE (e.g. an
    # interview follow-up) for an already-escalated candidate -- which
    # both defeats the point of escalating (a human should be handling
    # this, not the bot) and previously crashed outright, since
    # "escalated" only allows a narrow set of transitions.
    if candidate.get("status") == CandidateStatus.ESCALATED.value:
        logger.info(
            "Candidate %s (%s) is already escalated; skipping rule engine "
            "and LLM entirely, logging message for human review.",
            candidate_id, phone_number,
        )
        update_candidate_metadata(candidate_id, {"last_message_after_escalation_at": datetime.now(timezone.utc)})
        return {
            "escalated": True,
            "status": CandidateStatus.ESCALATED.value,
            "reply": None,
            "reason": (
                "Conversation is already escalated to a human. Rule engine and LLM "
                "were not called; no auto-reply was sent. Candidate's new message "
                "has been logged for the human reviewer."
            ),
        }

    # Run the deterministic rule engine
    rule_result = gate_message(message.body, candidate_state=state)

    # ---------------------------------------------------------------------------
    # Action: BLOCK_OPT_OUT
    # ---------------------------------------------------------------------------
    if rule_result.action == RuleAction.BLOCK_OPT_OUT:
        logger.info(
            "Candidate %s (%s) requested opt-out. Action: %s. Reason: %s", 
            candidate_id, phone_number, rule_result.action.value, rule_result.reason
        )
        transition_status(candidate_id, CandidateStatus.DECLINED.value)
        return {
            "escalated": False,
            "status": CandidateStatus.DECLINED.value,
            "reply": None,
            "reason": rule_result.reason,
        }

    # ---------------------------------------------------------------------------
    # Action: ESCALATE_TO_HUMAN
    # ---------------------------------------------------------------------------
    if rule_result.action == RuleAction.ESCALATE_TO_HUMAN:
        logger.info(
            "Candidate %s (%s) triggered sensitive topic escalation. Action: %s. Reason: %s", 
            candidate_id, phone_number, rule_result.action.value, rule_result.reason
        )
        transition_status(candidate_id, CandidateStatus.ESCALATED.value)
        now_ts = datetime.now(timezone.utc)
        if candidate.get("status") == CandidateStatus.ESCALATED.value:
            # Already escalated: don't overwrite the original reason with
            # a repeat trigger -- just note that a new message came in
            # after escalation, so a human knows there's unread activity.
            update_candidate_metadata(candidate_id, {"last_message_after_escalation_at": now_ts})
        else:
            # First time escalating: record both the reason and when.
            update_candidate_metadata(candidate_id, {
                "escalation_reason": rule_result.reason,
                "escalated_at": now_ts,
            })
        return {
            "escalated": True,
            "status": CandidateStatus.ESCALATED.value,
            "reply": None,
            "reason": rule_result.reason,
        }

    # ---------------------------------------------------------------------------
    # Action: NO_ACTION
    # ---------------------------------------------------------------------------
    if rule_result.action == RuleAction.NO_ACTION:
        logger.info(
            "No automated action required for candidate %s (%s). Action: %s. Reason: %s", 
            candidate_id, phone_number, rule_result.action.value, rule_result.reason
        )
        return {
            "escalated": False,
            "status": candidate.get("status", CandidateStatus.REPLIED.value),
            "reply": None,
            "reason": rule_result.reason,
        }

    # ---------------------------------------------------------------------------
    # Action: SEND_TEMPLATE
    # ---------------------------------------------------------------------------
    if rule_result.action == RuleAction.SEND_TEMPLATE:
        template_type = rule_result.template_type
        if not template_type:
            logger.error("Rule Engine returned SEND_TEMPLATE but no template_type was provided for candidate %s", candidate_id)
            return {
                "escalated": False,
                "status": candidate.get("status", CandidateStatus.REPLIED.value),
                "reply": None,
                "reason": "Error: Template action triggered but no template type provided.",
            }

        content_sid = TEMPLATE_SID_MAPPING.get(template_type)
        
        # Validate Template SID configuration boundary before hitting the messaging framework
        if not content_sid:
            logger.error("No Template SID configured for template type '%s'.", template_type.value)
            return {
                "escalated": False,
                "status": candidate.get("status", CandidateStatus.REPLIED.value),
                "reply": None,
                "reason": f"Missing Template SID for {template_type.value}.",
            }

        # Build template variables. Currently only JOB_OUTREACH has known
        # placeholders ({{1}} name, {{2}} role, {{3}} work type, {{4}} hours).
        # NO_REPLY/FINAL_REMINDER/INTERVIEW_FOLLOW_UP use {{1}} name and
        # {{2}} role, same as agent/followup_scheduler.py's
        # _build_followup_variables. This branch previously left
        # template_variables as None for those three types, which meant
        # Twilio silently fell back to its own stored SAMPLE content for
        # the template (e.g. a placeholder name like "Rajesh Kumar")
        # instead of the real candidate's name -- a real, live bug that
        # was reaching actual candidates, not just a test artifact.
        if template_type == TemplateType.JOB_OUTREACH:
            template_variables = _build_job_outreach_variables(candidate)
        else:
            template_variables = {
                "1": get_safe_display_name(candidate),
                "2": candidate.get("role") or "this role",
            }

        logger.info(
            "Preparing template send. Candidate ID: %s. Phone: %s. Action: %s. Type: %s. Content SID: %s. Reason: %s",
            candidate_id, phone_number, rule_result.action.value, template_type.value, content_sid, rule_result.reason
        )

        try:
            twilio_sid = send_template_message(
                to=phone_number,
                template_sid=content_sid,
                template_variables=template_variables,
                status_callback=STATUS_CALLBACK_URL,
            )
            logger.info("Successfully sent template. Candidate ID: %s. Type: %s. Twilio Message SID: %s", candidate_id, template_type.value, twilio_sid)
        except (MessagingConfigError, MessageSendError) as exc:
            logger.error("Twilio operational error for candidate %s while sending template: %s", candidate_id, exc, exc_info=True)
            return {
                "escalated": False,
                "status": candidate.get("status", CandidateStatus.REPLIED.value),
                "reply": None,
                "reason": f"Messaging error encountered while sending template: {exc}",
            }

        # Operational status and metadata roadmap tracking (safely using option B for string tracking variables)
        now_ts = datetime.now(timezone.utc)
        meta_updates = {"last_template_sent_at": now_ts}
        
        if template_type == TemplateType.JOB_OUTREACH:
            meta_updates["first_outreach_sent_at"] = now_ts
            next_status = "messaged"
        elif template_type == TemplateType.NO_REPLY:
            meta_updates["no_reply_template_sent"] = True
            next_status = "follow_up_sent"
        elif template_type == TemplateType.FINAL_REMINDER:
            meta_updates["final_reminder_sent"] = True
            next_status = "final_reminder_sent"
        elif template_type == TemplateType.INTERVIEW_FOLLOW_UP:
            next_status = "awaiting_interview"
        else:
            next_status = CandidateStatus.REPLIED.value

        # Also append this send to conversation_history so it shows up in
        # the dashboard's Messages tab -- this branch (SEND_TEMPLATE,
        # triggered by the rule engine in response to something the
        # candidate said) previously never touched conversation_history
        # at all, same gap as the initial-outreach and follow-up-scheduler
        # sends had before today's fixes.
        if template_type == TemplateType.JOB_OUTREACH:
            sent_text = (
                f"Hi {template_variables['1']}, this is Nisha from Amerisource Solutions Pvt. Ltd. "
                f"regarding your application status for the {template_variables['2']} role. "
                f"This is a {template_variables['3']} position with {template_variables['4']} working hours. "
                f"Please reply to this message to continue with your application. Thank you!"
            )
        else:
            sent_text = f"[{template_type.value} template sent]"
        meta_updates["conversation_history"] = (candidate.get("conversation_history") or []) + [
            {"role": "assistant", "content": sent_text}
        ]

        # Sync states safely through separate transition and explicit metadata updates
        transition_status(candidate_id, next_status)
        update_candidate_metadata(candidate_id, meta_updates)

        return {
            "escalated": False,
            "status": next_status,
            "reply": None,
            "reason": f"Template {template_type.value} dispatched successfully. {rule_result.reason}",
            "twilio_message_sid": twilio_sid,
        }

    # ---------------------------------------------------------------------------
    # Action: PASS_TO_LLM
    # ---------------------------------------------------------------------------
    conversation_history: Optional[list[dict[str, str]]] = candidate.get("conversation_history")

    # Safe parsing of the current database status into the CandidateStatus validation barrier
    raw_status = candidate.get("status")
    current_status = None
    if raw_status:
        try:
            current_status = CandidateStatus(raw_status.lower())
        except ValueError:
            pass

    # Call conversational AI generation layer
    result = process_message(
        candidate_message=message.body,
        conversation_history=conversation_history,
        current_status=current_status,
        candidate_name=candidate.get("name"),
    )

    # If the LLM just captured a real name (e.g. this candidate came in
    # via an unrecognized number and had no name on file, and just told
    # us their name), save it -- so future messages, template sends, and
    # the dashboard all use their real name instead of "Unknown"/the
    # phone number placeholder.
    if result.candidate_name:
        update_candidate_metadata(candidate_id, {"name": result.candidate_name})
        candidate["name"] = result.candidate_name

    if result.escalated:
        logger.info(
            "LLM layer flagged escalation. Candidate ID: %s. Phone: %s. Reason: %s", 
            candidate_id, phone_number, result.reason
        )
        transition_status(candidate_id, CandidateStatus.ESCALATED.value)
        now_ts = datetime.now(timezone.utc)
        if candidate.get("status") == CandidateStatus.ESCALATED.value:
            # Already escalated (this is the LLM-layer short-circuit path
            # for a candidate who messages again after being escalated).
            # Don't overwrite the original real reason with the generic
            # "already escalated" text -- just flag unread activity.
            update_candidate_metadata(candidate_id, {"last_message_after_escalation_at": now_ts})
        else:
            update_candidate_metadata(candidate_id, {
                "escalation_reason": result.reason,
                "escalated_at": now_ts,
            })
        return {
            "escalated": True,
            "status": CandidateStatus.ESCALATED.value,
            "reply": None,
            "reason": result.reason,
        }

    logger.info("Preparing text reply. Candidate ID: %s. Phone: %s. Intended Status: %s", candidate_id, phone_number, result.status.value)
    
    try:
        # send_text_message parameter mapping
        twilio_sid = send_text_message(to=phone_number, body=result.reply)
        logger.info("Successfully sent text response. Candidate ID: %s. Twilio Message SID: %s", candidate_id, twilio_sid)
    except (MessagingConfigError, MessageSendError) as exc:
        logger.error("Twilio operational error for candidate %s while sending conversational text: %s", candidate_id, exc, exc_info=True)
        return {
            "escalated": False,
            "status": candidate.get("status", CandidateStatus.REPLIED.value),
            "reply": None,
            "reason": f"Messaging error encountered while sending conversational text: {exc}",
        }

    # Persist conversation history so the LLM has context on the next
    # turn. Without this, conversation_history is always empty/None on
    # every reply, and the LLM re-introduces itself and re-asks the same
    # opening questions every single message, ignoring anything the
    # candidate already said.
    updated_history = list(conversation_history or [])
    updated_history.append({"role": "user", "content": message.body})
    updated_history.append({"role": "assistant", "content": result.reply})
    update_candidate_metadata(candidate_id, {"conversation_history": updated_history})

    # Complete state transition. Disallowed transitions (e.g. a
    # "declined" candidate keeps chatting and the LLM classifies the
    # reply as "replied") are caught here rather than crashing the whole
    # webhook request -- the candidate's status simply stays as-is, and
    # this is logged for visibility.
    try:
        transition_status(candidate_id, result.status.value)
    except ValueError as exc:
        logger.warning(
            "Skipped status transition for candidate %s: %s",
            candidate_id, exc,
        )

    return {
        "escalated": result.escalated,
        "status": result.status.value,
        "reply": result.reply,
        "reason": result.reason,
        "twilio_message_sid": twilio_sid,
    }
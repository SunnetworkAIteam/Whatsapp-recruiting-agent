"""
agent/pipeline.py

Implements handle_candidate_reply -- the function webhook/orchestrator.py's
set_message_processor() is wired up to via:

    from webhook.orchestrator import set_message_processor
    from agent.pipeline import handle_candidate_reply
    set_message_processor(handle_candidate_reply)

(see README.md, "Integrating the agent module")

Flow: webhook receives a reply -> rule_engine.gate_message() -> if it
passes, llm_layer.process_message() -> transition_status() records the
outcome in Supabase -> reply is sent back out (or logged, until Twilio
sending is wired up).

If gate_message() does NOT return PASS_TO_LLM, this stops immediately:
no LLM call, no outbound send.
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.llm_layer import process_message
from agent.rule_engine import RuleAction, gate_message
from app.status import get_candidate_by_phone, record_inbound_message, transition_status
from webhook.schemas import TwilioInboundMessage

logger = logging.getLogger("agent.pipeline")


def _send_reply(to_number: str, body: str) -> None:
    """
    Send the reply back to the candidate over WhatsApp.

    Twilio's REST API call isn't wired up yet (that's Sanjan's part per the
    original spec), so for now this just logs what would be sent. Swap the
    body of this function for a real Twilio client call once that's ready;
    callers don't need to change.
    """
    logger.info("Would send WhatsApp reply to %s: %s", to_number, body)


def handle_candidate_reply(message: TwilioInboundMessage) -> dict:
    """
    Orchestrates a single inbound candidate reply end-to-end.

    Returns a dict describing the outcome, as required by
    webhook/orchestrator.py's `process_inbound_message`.
    """
    candidate = get_candidate_by_phone(message.from_number)

    if candidate is None:
        # No matching candidate record -- nothing to transition, nothing
        # safe to auto-reply to. Log and stop; this shouldn't happen in
        # practice since candidates are only messaged after being uploaded
        # via /upload-excel, but guard against it rather than crashing.
        logger.warning(
            "No candidate found for phone=%s (sid=%s); dropping message.",
            message.from_number,
            message.message_sid,
        )
        return {
            "escalated": False,
            "status": "unknown",
            "reply": None,
            "reason": "No matching candidate record for this phone number.",
        }

    candidate_id = candidate["id"]
    last_candidate_message_at = candidate.get("last_candidate_message_at")

    # Step 2: run the deterministic rule engine before anything else.
    rule_result = gate_message(message.body, last_candidate_message_at)

    if rule_result.action == RuleAction.BLOCK_OPT_OUT:
        transition_status(candidate_id, "declined")
        return {
            "escalated": False,
            "status": "declined",
            "reply": None,
            "reason": rule_result.reason,
        }

    if rule_result.action == RuleAction.ESCALATE_TO_HUMAN:
        transition_status(candidate_id, "escalated")
        return {
            "escalated": True,
            "status": "escalated",
            "reply": None,
            "reason": rule_result.reason,
        }

    if rule_result.action == RuleAction.SEND_TEMPLATE:
        # 24h window expired. Twilio template sending isn't wired up yet
        # (Sanjan's part) -- log it and leave the candidate's status as-is
        # rather than guessing a new one.
        logger.info(
            "Reply window expired for candidate_id=%s; template send required: %s",
            candidate_id,
            rule_result.reason,
        )
        return {
            "escalated": False,
            "status": candidate.get("status", "replied"),
            "reply": None,
            "reason": rule_result.reason,
        }

    # rule_result.action == RuleAction.PASS_TO_LLM: continue to the LLM layer.
    record_inbound_message(candidate_id)

    conversation_history: Optional[list[dict[str, str]]] = candidate.get("conversation_history")
    result = process_message(message.body, conversation_history)

    if result.escalated:
        transition_status(candidate_id, "escalated")
        return {
            "escalated": True,
            "status": "escalated",
            "reply": None,
            "reason": result.reason,
        }

    transition_status(candidate_id, result.status.value)
    _send_reply(message.from_number, result.reply)

    return {
        "escalated": result.escalated,
        "status": result.status.value,
        "reply": result.reply,
        "reason": result.reason,
    }
"""
Inbound webhook for candidate WhatsApp replies (Twilio -> this endpoint).

Flow per the project README:
    Twilio POSTs the reply -> this webhook -> idempotency check ->
    handed off to the agent pipeline (rule engine, then LLM if needed) ->
    HR dashboard reflects the updated status.

Design notes:
- Twilio expects a fast response (a few seconds) and retries on timeout or
  non-2xx, resending the identical MessageSid — hence the idempotency
  check happens before any real work, and this endpoint responds with
  empty TwiML immediately. Any WhatsApp reply back to the candidate is
  sent asynchronously afterwards (via the Twilio REST API from the agent
  pipeline / a queue worker), not inline in this response.
- Signature verification can be disabled for local dev / tests via
  `TWILIO_VALIDATE_SIGNATURE=false`, since you won't have a public HTTPS
  URL for Twilio to sign against on localhost.
"""





from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import Response

from .idempotency import IdempotencyStore
from .orchestrator import process_inbound_message
from .schemas import TwilioInboundMessage
from .security import is_valid_twilio_signature

logger = logging.getLogger("webhook")

app = FastAPI(title="WhatsApp Candidate Reply Webhook")


@app.on_event("startup")
def _register_agent_pipeline():
    """
    Wire the real rule-engine/LLM pipeline into the orchestrator.
    Needed here because this module can run standalone
    (uvicorn webhook.app:app), in which case app/main.py's own startup
    hook never runs and the orchestrator silently falls back to
    _default_processor, which does nothing.
    """
    from .orchestrator import set_message_processor
    from agent.pipeline import handle_candidate_reply
    set_message_processor(handle_candidate_reply)
    logger.info("Registered agent.pipeline.handle_candidate_reply as the message processor.")

# 24h TTL: comfortably covers Twilio's retry window. Replace with a
# Redis-backed store for production / multi-worker deployments.
idempotency_store = IdempotencyStore(ttl_seconds=24 * 60 * 60)

EMPTY_TWIML_RESPONSE = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _validate_signature_enabled() -> bool:
    return os.getenv("TWILIO_VALIDATE_SIGNATURE", "true").lower() not in ("false", "0", "no")


@app.post("/webhooks/whatsapp/inbound")
async def whatsapp_inbound_webhook(request: Request):
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    if _validate_signature_enabled():
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        signature = request.headers.get("X-Twilio-Signature", "")
        # Twilio signs the exact public URL it called, which can differ from
        # request.url when behind a proxy/load balancer. TWILIO_WEBHOOK_URL
        # should be set to the exact URL configured in the Twilio console.
        webhook_url = os.getenv("TWILIO_WEBHOOK_URL") or str(request.url)

        if not auth_token or not is_valid_twilio_signature(auth_token, webhook_url, params, signature):
            logger.warning("Rejected webhook request with invalid Twilio signature")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    if "MessageSid" not in params:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing MessageSid")

    try:
        message = TwilioInboundMessage(**params)
    except Exception as exc:  # pydantic ValidationError, kept generic on purpose
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # MessageSid is Twilio's own unique ID per message/delivery attempt —
    # the natural idempotency key. Reserve it atomically before doing any
    # work, so two racing retries can't both slip through.
    _, is_new = idempotency_store.save_if_absent(message.message_sid, response_body={"status": "queued"})

    if not is_new:
        logger.info("Duplicate delivery for MessageSid=%s; skipping reprocessing", message.message_sid)
        return Response(content=EMPTY_TWIML_RESPONSE, media_type="application/xml")

    result = process_inbound_message(message)
    logger.info("Processed inbound message sid=%s result=%s", message.message_sid, result)

    return Response(content=EMPTY_TWIML_RESPONSE, media_type="application/xml")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhooks/whatsapp/status")
async def whatsapp_status_callback(request: Request):
    """
    Receives delivery status updates from Twilio for outbound messages
    (set via the status_callback param on send_template_message --
    see agent/send_functions.py and app/tasks.py).

    Twilio doesn't offer a direct "does this number have WhatsApp" check
    up front; the real-world signal is that a message sent to a number
    without WhatsApp comes back with status "failed" or "undelivered"
    (with an error code) some time after sending, delivered here.

    When that happens, this flags the candidate's platform column as
    "no_whatsapp" and stores the raw error for a human to review --
    rather than guessing the exact meaning of every possible Twilio error
    code, any failed/undelivered status is treated as "couldn't reach
    this candidate via WhatsApp" and surfaced for manual follow-up.
    """
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    message_sid = params.get("MessageSid", "")
    message_status = params.get("MessageStatus", "")
    error_code = params.get("ErrorCode", "")
    error_message = params.get("ErrorMessage", "")
    to_number = params.get("To", "")

    logger.info(
        "Status callback: sid=%s status=%s error_code=%s to=%s",
        message_sid, message_status, error_code, to_number,
    )

    if message_status in ("failed", "undelivered") and to_number:
        from app.validation import normalize_phone
        from app.status import get_candidate_by_phone, update_candidate_metadata

        normalized_phone = normalize_phone(to_number)
        candidate = get_candidate_by_phone(normalized_phone)

        if candidate:
            update_candidate_metadata(candidate["id"], {
                "platform": "no_whatsapp",
                "delivery_status": message_status,
                "delivery_error_code": error_code,
                "delivery_error_message": error_message,
            })
            logger.warning(
                "Candidate %s flagged as no_whatsapp (error_code=%s, message=%s)",
                candidate["id"], error_code, error_message,
            )
        else:
            logger.warning(
                "Received failed delivery status for unrecognized number (sid=%s); "
                "no matching candidate found.",
                message_sid,
            )

    return Response(status_code=204)

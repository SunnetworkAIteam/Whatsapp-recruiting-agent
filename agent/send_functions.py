"""
messaging.py

Messaging (Twilio send) layer for the WhatsApp Recruiting Agent.

This module knows only HOW to send WhatsApp messages via Twilio and how
to check the WhatsApp 24-hour customer service reply window. It contains
no business logic: it does not decide whether to send a text message or 
a template, and it never decides which template should be sent. 

The Rule Engine selects the appropriate template and passes the
corresponding Twilio Content SID to this module.

Supported template types include:
    * Initial Job Outreach
    * No Reply Reminder
    * Final Reminder
    * Interview Follow-up

Responsibilities:
    * Send an approved WhatsApp template message via Twilio.
    * Send a WhatsApp text message via Twilio.
    * Determine whether the most recent inbound message from a candidate
      falls within the last 24 hours.

Out of scope (intentionally, per architecture):
    * Deciding SEND_TEMPLATE vs. PASS_TO_LLM / text.
    * Selecting specific templates based on candidate states.
    * Any first-contact / conversation-state tracking.
    These all live in the Rule Engine.

Logging is not configured here; the application configures logging
globally. This module only acquires a named logger.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from dotenv import load_dotenv
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.rest import Client

# --------------------------------------------------------------------------
# Environment / configuration
# --------------------------------------------------------------------------

load_dotenv()

# Core Twilio credentials
TWILIO_ACCOUNT_SID: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER: Optional[str] = os.getenv("TWILIO_WHATSAPP_NUMBER")

# Recruiting Workflow Template SIDs (Loaded here only; transport layer does not select them)
TWILIO_JOB_OUTREACH_TEMPLATE_SID: Optional[str] = os.getenv(
    "TWILIO_JOB_OUTREACH_TEMPLATE_SID"
)
TWILIO_INTERVIEW_FOLLOW_UP_TEMPLATE_SID: Optional[str] = os.getenv(
    "TWILIO_INTERVIEW_FOLLOW_UP_TEMPLATE_SID"
)
TWILIO_NO_REPLY_TEMPLATE_SID: Optional[str] = os.getenv(
    "TWILIO_NO_REPLY_TEMPLATE_SID"
)
TWILIO_FINAL_REMINDER_TEMPLATE_SID: Optional[str] = os.getenv(
    "TWILIO_FINAL_REMINDER_TEMPLATE_SID"
)

# The WhatsApp 24-hour customer service window, per Twilio/WhatsApp policy.
WHATSAPP_REPLY_WINDOW = timedelta(hours=24)

# Twilio Content SIDs conventionally start with this prefix.
_CONTENT_SID_PREFIX = "HX"

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

# The application configures logging globally (handlers, formatters,
# levels). This module only acquires a logger by name.
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class MessagingConfigError(RuntimeError):
    """Raised when required Twilio configuration is missing or invalid."""


class MessageSendError(RuntimeError):
    """Raised when Twilio fails to send a message (template or text message)."""


# --------------------------------------------------------------------------
# Twilio client initialization
# --------------------------------------------------------------------------

def _validate_core_config() -> None:
    """
    Validate that the core Twilio credentials and sender number are set.

    Raises:
        MessagingConfigError: If any required environment variable for
            basic Twilio operation is missing.
    """
    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID),
            ("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN),
            ("TWILIO_WHATSAPP_NUMBER", TWILIO_WHATSAPP_NUMBER),
        )
        if not value
    ]
    if missing:
        raise MessagingConfigError(
            "Missing required environment variable(s): "
            f"{', '.join(missing)}. Check your .env file."
        )


def _get_twilio_client() -> Client:
    """
    Build and return a Twilio REST client using the configured credentials.

    Returns:
        Client: An authenticated Twilio REST client instance.

    Raises:
        MessagingConfigError: If required credentials are missing.
    """
    _validate_core_config()
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# A single module-level client, lazily created on first use so that
# importing this module never fails just because .env isn't loaded yet
# (e.g. during static analysis or partial test setups).
_client: Optional[Client] = None


def _client_singleton() -> Client:
    """Return a cached Twilio client, creating it on first call."""
    global _client
    if _client is None:
        _client = _get_twilio_client()
    return _client


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _format_whatsapp_address(phone_number: str) -> str:
    """
    Ensure a phone number is prefixed with the ``whatsapp:`` scheme
    required by Twilio's WhatsApp channel.

    Args:
        phone_number: A phone number, with or without the ``whatsapp:``
            prefix, e.g. ``"+15551234567"`` or ``"whatsapp:+15551234567"``.

    Returns:
        str: The phone number prefixed with ``whatsapp:``.
    """
    phone_number = phone_number.strip()
    if phone_number.startswith("whatsapp:"):
        return phone_number
    return f"whatsapp:{phone_number}"


def _build_content_variables(
    template_variables: Optional[Mapping[str, Any]]
) -> Optional[str]:
    """
    Serialize template variables into the JSON string format expected by
    Twilio's ``content_variables`` parameter for WhatsApp templates.

    Args:
        template_variables: Mapping of variable index/name to a
            JSON-compatible value, e.g. ``{"1": "Priya", "2": 3}``. May
            be ``None``.

    Returns:
        Optional[str]: A JSON-encoded string, or ``None`` if no variables
        were provided.
    """
    if not template_variables:
        return None
    return json.dumps(dict(template_variables))


def _validate_template_sid(template_sid: str) -> None:
    """
    Validate that a template configuration SID is present and looks like 
    a Twilio Content SID.

    Args:
        template_sid: The Content SID to validate.

    Raises:
        MessagingConfigError: If ``template_sid`` is missing or empty.
    """
    if not template_sid or not template_sid.strip():
        raise MessagingConfigError("The requested WhatsApp template SID configuration is missing or empty.")

    if not template_sid.startswith(_CONTENT_SID_PREFIX):
        logger.warning(
            "template_sid '%s' does not start with the expected '%s' "
            "prefix used by Twilio Content SIDs; proceeding, but this "
            "may indicate a misconfigured template SID.",
            template_sid,
            _CONTENT_SID_PREFIX,
        )


# --------------------------------------------------------------------------
# Public API: sending messages
# --------------------------------------------------------------------------

def send_template_message(
    to: str,
    template_sid: str,
    template_variables: Optional[Mapping[str, Any]] = None,
    message_type: str = "UNKNOWN",
) -> str:
    """
    Send an approved WhatsApp template message via Twilio.

    The caller (the Rule Engine) is responsible for deciding when a
    template must be sent and which template SID to use. Selected template
    supplied by the Rule Engine. This function only performs the transport send.

    Args:
        to: The candidate's phone number, e.g. ``"+15551234567"``. The
            ``whatsapp:`` scheme is added automatically if not present.
        template_sid: The approved Twilio Content SID for the template to
            send. Must be non-empty; validated by an internal helper.
        template_variables: Optional mapping of template variable
            placeholders to JSON-compatible values, e.g.
            ``{"1": "Priya", "2": 3}``.
        message_type: Strategic classification label supplied by the Rule Engine
            for distinct logging context observability (e.g. JOB_OUTREACH, NO_REPLY).

    Returns:
        str: The Twilio Message SID of the sent message.

    Raises:
        MessagingConfigError: If Twilio credentials or template SIDs are missing.
        ValueError: If ``to`` is empty.
        MessageSendError: If Twilio rejects or fails to send the message.
    """
    if not to:
        raise ValueError("`to` (recipient phone number) must not be empty.")
    _validate_template_sid(template_sid)

    client = _client_singleton()
    recipient = _format_whatsapp_address(to)
    sender = _format_whatsapp_address(TWILIO_WHATSAPP_NUMBER)
    content_variables = _build_content_variables(template_variables)

    logger.info(
        "Sending WhatsApp template | recipient=%s | template_sid=%s | message_type=%s",
        recipient,
        template_sid,
        message_type,
    )

    try:
        kwargs = {
            "from_": sender,
            "to": recipient,
            "content_sid": template_sid,
        }
        if content_variables is not None:
            kwargs["content_variables"] = content_variables

        message = client.messages.create(**kwargs)
    except TwilioRestException as exc:
        logger.error(
            "Twilio rejected template message | to=%s | template_sid=%s | "
            "code=%s | message=%s",
            recipient,
            template_sid,
            exc.code,
            exc.msg,
        )
        raise MessageSendError(
            f"Failed to send template message (SID {template_sid}) to "
            f"{recipient}: Twilio error {exc.code} - {exc.msg}"
        ) from exc
    except TwilioException as exc:
        logger.error(
            "Twilio client error while sending template message | to=%s | "
            "template_sid=%s | error=%s",
            recipient,
            template_sid,
            exc,
        )
        raise MessageSendError(
            f"Unexpected Twilio client error while sending template "
            f"message to {recipient}: {exc}"
        ) from exc

    logger.info(
        "Template message sent | to=%s | template_sid=%s | message_sid=%s",
        recipient,
        template_sid,
        message.sid,
    )
    return message.sid


def send_text_message(to: str, body: str) -> str:
    """
    Send a WhatsApp text message via Twilio.

    The caller (the Rule Engine) is responsible for ensuring the
    candidate is within the 24-hour reply window (see
    ``is_within_reply_window``) before invoking this function. Sending
    plain text messages outside that window will be rejected by
    WhatsApp/Twilio.

    Args:
        to: The candidate's phone number, e.g. ``"+15551234567"``. The
            ``whatsapp:`` scheme is added automatically if not present.
        body: The plain text message to send. Must not be empty.

    Returns:
        str: The Twilio Message SID of the sent message.

    Raises:
        MessagingConfigError: If Twilio credentials/sender are missing.
        ValueError: If ``to`` or ``body`` is empty.
        MessageSendError: If Twilio rejects or fails to send the message.
    """
    if not to:
        raise ValueError("`to` (recipient phone number) must not be empty.")
    if not body or not body.strip():
        raise ValueError("`body` (message text) must not be empty.")

    client = _client_singleton()
    recipient = _format_whatsapp_address(to)
    sender = _format_whatsapp_address(TWILIO_WHATSAPP_NUMBER)

    logger.info("Sending WhatsApp text message | to=%s", recipient)

    try:
        message = client.messages.create(from_=sender, to=recipient, body=body)
    except TwilioRestException as exc:
        logger.error(
            "Twilio rejected text message | to=%s | code=%s | "
            "message=%s",
            recipient,
            exc.code,
            exc.msg,
        )
        raise MessageSendError(
            f"Failed to send text message to {recipient}: "
            f"Twilio error {exc.code} - {exc.msg}"
        ) from exc
    except TwilioException as exc:
        logger.error(
            "Twilio client error while sending text message | to=%s | "
            "error=%s",
            recipient,
            exc,
        )
        raise MessageSendError(
            f"Unexpected Twilio client error while sending text "
            f"message to {recipient}: {exc}"
        ) from exc

    logger.info(
        "Text message sent | to=%s | message_sid=%s", recipient, message.sid
    )
    return message.sid


# --------------------------------------------------------------------------
# Public API: 24-hour window check
# --------------------------------------------------------------------------

def is_within_reply_window(last_inbound_message_at: datetime) -> bool:
    """
    Determine whether a candidate's most recent inbound WhatsApp message
    falls within the last 24 hours (the WhatsApp customer service window).

    This function performs a check only; it never sends any messages.

    Args:
        last_inbound_message_at: The timezone-aware timestamp of the
            candidate's most recent inbound WhatsApp message.

    Returns:
        bool: ``True`` if the last inbound message was received within the
        past 24 hours, ``False`` otherwise.

    Raises:
        ValueError: If ``last_inbound_message_at`` is ``None``, or is a
            naive datetime (no ``tzinfo``). Callers must supply a
            timezone-aware datetime; naive datetimes are never silently
            assumed to be UTC.
    """
    if last_inbound_message_at is None:
        raise ValueError("`last_inbound_message_at` must not be None.")

    if last_inbound_message_at.tzinfo is None:
        raise ValueError(
            "`last_inbound_message_at` must be timezone-aware; naive "
            "datetimes are not accepted. Attach an explicit tzinfo "
            "(e.g. `datetime.now(timezone.utc)`) before calling this "
            "function."
        )

    now = datetime.now(timezone.utc)
    elapsed = now - last_inbound_message_at

    within_window = timedelta(0) <= elapsed < WHATSAPP_REPLY_WINDOW

    logger.debug(
        "24h window check | last_inbound_at=%s | now=%s | elapsed=%s | "
        "within_window=%s",
        last_inbound_message_at.isoformat(),
        now.isoformat(),
        elapsed,
        within_window,
    )
    return within_window


# --------------------------------------------------------------------------
# Module exports
# --------------------------------------------------------------------------

__all__ = [
    "send_template_message",
    "send_text_message",
    "is_within_reply_window",
]
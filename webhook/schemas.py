"""
Schema for inbound WhatsApp messages delivered by Twilio's webhook.

Twilio posts these as application/x-www-form-urlencoded fields, not JSON.
Only the fields we actually use are modeled here; Twilio sends many more
(NumMedia, ProfileName, WaId, etc.) which can be added as needed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TwilioInboundMessage(BaseModel):
    message_sid: str = Field(..., alias="MessageSid")
    from_number: str = Field(..., alias="From")   # e.g. "whatsapp:+14155551234"
    to_number: str = Field(..., alias="To")
    body: str = Field(default="", alias="Body")

    model_config = {
        "populate_by_name": True,
    }

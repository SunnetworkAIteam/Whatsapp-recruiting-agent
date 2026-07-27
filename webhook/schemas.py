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
    # Media attachment fields (e.g. a candidate sending their resume).
    # Twilio only sends MediaUrl0/MediaContentType0 when NumMedia > 0 --
    # a text-only message simply omits them, so these default to
    # None/"0" rather than being required.
    num_media: str = Field(default="0", alias="NumMedia")
    media_url_0: str | None = Field(default=None, alias="MediaUrl0")
    media_content_type_0: str | None = Field(default=None, alias="MediaContentType0")

    model_config = {
        "populate_by_name": True,
    }

    @property
    def has_media(self) -> bool:
        try:
            return int(self.num_media) > 0 and bool(self.media_url_0)
        except ValueError:
            return False

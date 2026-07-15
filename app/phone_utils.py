"""
app/phone_utils.py

Shared helper for formatting stored candidate phone numbers into E.164
before handing them to Twilio.

Candidates are stored with bare 10-digit Indian numbers (see
app/validation.py's normalize_phone). Twilio needs a real E.164 number to
route WhatsApp messages correctly -- without a country code, Twilio's
client silently defaults to +1 (US), producing an "undelivered" message
with no exception raised in calling code. This actually happened once:
a follow-up send went to whatsapp:+16302082273 instead of
+916302082273 (Twilio error code 63024).

Used by app/tasks.py (initial outreach) and agent/followup_scheduler.py
(follow-ups) -- any code that sends to a candidate['phone'] pulled
directly from the DB should go through this first.
"""


def to_e164_india(phone: str) -> str:
    """
    Assumes India (+91) since that's this project's candidate base. If
    candidates from other countries are ever added, this needs to become
    smarter (e.g. a country code column) rather than a hardcoded +91
    prefix.
    """
    phone = (phone or "").strip()
    if phone.startswith("+"):
        return phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    return f"+{digits}"  # best effort if it's some other length/format
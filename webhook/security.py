"""
Verifies that an inbound webhook request genuinely came from Twilio.

Twilio signs each webhook request with your Auth Token: it takes the full
request URL, appends each POST param (sorted by key, key+value concatenated
with no separator), HMAC-SHA1s that string with your Auth Token, base64
encodes the result, and sends it as the `X-Twilio-Signature` header. We
recompute the same thing and compare.

Reference: https://www.twilio.com/docs/usage/security#validating-requests
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping


def compute_twilio_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]

    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def is_valid_twilio_signature(
    auth_token: str,
    url: str,
    params: Mapping[str, str],
    signature_header: str,
) -> bool:
    if not signature_header:
        return False
    expected = compute_twilio_signature(auth_token, url, params)
    # constant-time comparison to avoid timing attacks
    return hmac.compare_digest(expected, signature_header)

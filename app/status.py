<<<<<<< HEAD
"""
app/status.py

Candidate status transitions, persisted to Supabase.

This is the single place that writes a candidate's status to the
`candidates` table. Both the rule engine path and the LLM path in
agent/pipeline.py call transition_status() rather than touching Supabase
directly, so there is exactly one place that knows the table/column names.

ASSUMPTIONS (adjust if your actual `candidates` table differs):
- Table name: "candidates"
- Primary key column: "id"
- Phone number column: "phone" (matches app/validation.py's normalize_phone
  output -- last 10 digits, no formatting)
- A "status" text column exists to store one of:
  replied / qualified / escalated / declined
- A "last_candidate_message_at" timestamp column exists, used by
  agent/rule_engine.py's check_reply_window() to enforce WhatsApp's 24h
  free-form messaging window.

If your table uses different column names, update the dict keys in
get_candidate_by_phone() and transition_status() only -- callers in
agent/pipeline.py don't need to change.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from supabase import Client, create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_supabase: Optional[Client] = None


def get_client() -> Client:
    """
    Lazily create (and cache) the Supabase client, so importing this module
    doesn't require env vars to already be set (useful for tests that pass
    their own client in).
    """
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def get_candidate_by_phone(phone: str, client: Optional[Client] = None) -> Optional[dict]:
    """
    Look up a candidate by their normalized phone number.

    phone should already be normalized the same way app/validation.py's
    normalize_phone() does it, since that's how numbers are stored on
    insert (see app/main.py's /upload-excel handler).

    Returns the candidate row as a dict, or None if no match is found.
    """
    client = client or get_client()
    result = (
        client.table("candidates")
        .select("*")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def transition_status(
    candidate_id: str,
    new_status: str,
    client: Optional[Client] = None,
) -> dict:
    """
    Update a candidate's status in Supabase.

    candidate_id  The candidate row's primary key (the "id" column).
    new_status    One of: "replied", "qualified", "escalated", "declined"
                  (see agent.llm_layer.CandidateStatus for the canonical
                  values -- pass result.status.value, not the enum itself).

    Returns the updated row as a dict. Raises whatever the Supabase client
    raises on a failed request; callers should let that propagate so
    failures aren't silently swallowed.
    """
    client = client or get_client()
    result = (
        client.table("candidates")
        .update({"status": new_status})
        .eq("id", candidate_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def record_inbound_message(
    candidate_id: str,
    received_at: Optional[datetime] = None,
    client: Optional[Client] = None,
) -> dict:
    """
    Stamp the timestamp of the candidate's latest inbound message.

    This is what agent.rule_engine.check_reply_window() reads back (via
    last_candidate_message_at) to decide whether WhatsApp's 24-hour
    free-form reply window is still open. Call this once per inbound
    message, before running gate_message().
    """
    client = client or get_client()
    timestamp = (received_at or datetime.now(timezone.utc)).isoformat()
    result = (
        client.table("candidates")
        .update({"last_candidate_message_at": timestamp})
        .eq("id", candidate_id)
        .execute()
    )
    return result.data[0] if result.data else {}
=======
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

VALID_STATUSES = [
    "pending", "messaged", "replied", "qualified",
    "escalated", "booked", "declined"
]

ALLOWED_TRANSITIONS = {
    "pending": ["messaged", "declined"],
    "messaged": ["replied", "declined"],
    "replied": ["qualified", "escalated", "declined"],
    "qualified": ["booked", "declined"],
    "escalated": ["booked", "declined"],
    "booked": [],       # final state
    "declined": []      # final state
}

def transition_status(candidate_id: int, new_status: str):
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    if supabase is None:
        raise RuntimeError("Supabase client not initialized")

    result = supabase.table("candidates").select("status").eq("id", candidate_id).execute()

    # Supabase returns an object with .data
    if not hasattr(result, "data") or not result.data:
        raise ValueError(f"Candidate {candidate_id} not found")

    current_status = result.data[0]["status"]

    if new_status not in ALLOWED_TRANSITIONS.get(current_status, []):
        raise ValueError(f"Cannot transition from {current_status} to {new_status}")

    supabase.table("candidates").update({"status": new_status}).eq("id", candidate_id).execute()
    return f"Candidate {candidate_id} transitioned from {current_status} to {new_status}"
>>>>>>> e608cbb92117e87578082c2a587657a64079e0da

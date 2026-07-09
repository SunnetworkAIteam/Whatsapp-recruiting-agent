import os
from datetime import datetime, timezone
from typing import Optional

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

    if not hasattr(result, "data") or not result.data:
        raise ValueError(f"Candidate {candidate_id} not found")

    current_status = result.data[0]["status"]

    if new_status not in ALLOWED_TRANSITIONS.get(current_status, []):
        raise ValueError(f"Cannot transition from {current_status} to {new_status}")

    supabase.table("candidates").update({"status": new_status}).eq("id", candidate_id).execute()
    return f"Candidate {candidate_id} transitioned from {current_status} to {new_status}"


def get_candidate_by_phone(phone: str) -> Optional[dict]:
    """
    Look up a candidate by their normalized phone number.
    Used by agent/pipeline.py to resolve a candidate_id from an inbound
    WhatsApp message before calling transition_status().
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")

    result = supabase.table("candidates").select("*").eq("phone", phone).limit(1).execute()
    return result.data[0] if result.data else None


def record_inbound_message(candidate_id: int, received_at: Optional[datetime] = None) -> dict:
    """
    Stamp the timestamp of the candidate's latest inbound message.
    Used by agent/rule_engine.py's check_reply_window() to enforce
    WhatsApp's 24-hour free-form messaging window.
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")

    timestamp = (received_at or datetime.now(timezone.utc)).isoformat()
    result = (
        supabase.table("candidates")
        .update({"last_candidate_message_at": timestamp})
        .eq("id", candidate_id)
        .execute()
    )
    return result.data[0] if result.data else {}

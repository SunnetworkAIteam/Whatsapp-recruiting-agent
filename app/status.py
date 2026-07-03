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

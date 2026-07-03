from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

def enqueue_pending_candidates():
    if supabase is None:
        raise RuntimeError("Supabase URL/Key not set. Check your .env file (enqueue_pending_candidates).")

    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
    for row in result.data:
        send_candidate.delay(row["id"])

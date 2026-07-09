from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

def enqueue_pending_candidates():
    global supabase
    if supabase is None:
        raise RuntimeError("Supabase not configured.")
    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
    for row in (result.data or []):
        send_candidate.delay(row["id"])

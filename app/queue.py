from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Expose module-level variable so tests can monkeypatch app.queue.supabase
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def enqueue_pending_candidates():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return  # Supabase not configured

    global supabase
    if supabase is None:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
    # defensive: result.data might be None
    for row in (result.data or []):
        send_candidate.delay(row["id"])

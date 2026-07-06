from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Expose module-level variable so tests can monkeypatch app.queue.supabase
supabase = None

def enqueue_pending_candidates():
    result = supabase.table("candidates").select("*").eq("status", "pending").execute()
    
    for candidate in result.data:
        send_candidate.delay(candidate["id"])

    global supabase
    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
   

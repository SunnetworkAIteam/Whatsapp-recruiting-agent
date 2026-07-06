from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = None
# Expose module-level variable so tests can monkeypatch app.queue.supabase

def enqueue_pending_candidates():

    global supabase
    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
   

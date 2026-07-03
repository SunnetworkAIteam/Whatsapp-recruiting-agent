from app.tasks import send_candidate
from supabase import create_client
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def enqueue_pending_candidates():
    result = supabase.table("candidates").select("id").eq("status", "pending").execute()
    for row in result.data:
        send_candidate.delay(row["id"])

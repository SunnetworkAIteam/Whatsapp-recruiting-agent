from app.tasks import send_candidate
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

def enqueue_pending_candidates():    
    # Query pending candidates from Supabase
    result = supabase.table("candidates").select("*").eq("status", "pending").execute()
    
    # Iterate through results and enqueue each candidate
   # for candidate in result.data:
    #    send_candidate.delay(candidate["id"])

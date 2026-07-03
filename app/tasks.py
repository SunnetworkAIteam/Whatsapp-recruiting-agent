from app.celery_app import celery_app
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

@celery_app.task(bind=True, max_retries=3)
def send_candidate(self, candidate_id: int):
    global supabase
    if supabase is None:
        if SUPABASE_URL and SUPABASE_KEY:
            from supabase import create_client as _create_client
            supabase = _create_client(SUPABASE_URL, SUPABASE_KEY)
        else:
            raise RuntimeError("Supabase URL/Key not set. Check your .env file.")

    try:
        result = supabase.table("candidates").select("*").eq("id", candidate_id).eq("status", "pending").execute()
        if not result.data:
            return f"Candidate {candidate_id} not pending"

        candidate = result.data[0]
        print(f"Sending candidate: {candidate.get('name')} - {candidate.get('phone')}")
        supabase.table("candidates").update({"status": "messaged"}).eq("id", candidate_id).execute()
        return f"Candidate {candidate_id} sent successfully"
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

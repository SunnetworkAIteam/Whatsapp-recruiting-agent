from app.celery_app import celery_app
import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables first
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Supabase URL/Key not set. Check your .env file.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@celery_app.task(bind=True, max_retries=3)
def send_candidate(self, candidate_id: int):
    try:
        result = supabase.table("candidates").select("*").eq("id", candidate_id).eq("status", "pending").execute()
        if not result.data:
            return f"Candidate {candidate_id} not pending"

        candidate = result.data[0]

        # Simulate sending (replace with Twilio/email integration later)
        print(f"Sending candidate: {candidate['name']} - {candidate['phone']}")

        supabase.table("candidates").update({"status": "sent"}).eq("id", candidate_id).execute()
        return f"Candidate {candidate_id} sent successfully"

    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

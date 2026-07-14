import os
from dotenv import load_dotenv
from fastapi import FastAPI
from supabase import create_client
from app.validation import validate_candidate, normalize_phone, is_duplicate
from app.google_sheets import fetch_all_candidates_from_sheets

load_dotenv()

app = FastAPI()


@app.on_event("startup")
def startup():
    from webhook.orchestrator import set_message_processor
    from agent.pipeline import handle_candidate_reply
    set_message_processor(handle_candidate_reply)


# Supabase client (read from environment variables)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.post("/sync-sheets")
async def sync_sheets():
    """
    Reads every Google Sheet in the configured Drive folder, validates and
    dedupes each row using the same logic as the Excel path, and inserts new
    candidates into Supabase. Each sheet's name becomes the candidate's role.
    """
    try:
        records, warnings = fetch_all_candidates_from_sheets()
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not read from Google Sheets: {str(e)}"}

    inserted, skipped = [], []

    for record in records:
        if not validate_candidate(record):
            skipped.append({"reason": "invalid", "record": record})
            continue

        normalized = normalize_phone(record["phone"])
        if is_duplicate(supabase, normalized):
            skipped.append({"reason": "duplicate", "record": record})
            continue

        # Build the insert payload from every schema column present in the row,
        # falling back to None for anything the sheet didn't provide.
        payload = {
            "name": record.get("name"),
            "phone": normalized,
            "role": record.get("role"),
            "location": record.get("location"),
            "email": record.get("email"),
            "current_company": record.get("current_company"),
            "college_name": record.get("college_name"),
            "source": record.get("source"),
            "hiring_recruiter_name": record.get("hiring_recruiter_name"),
            "remarks": record.get("remarks"),
            "interview_score": record.get("interview_score") or None,
            "communication": record.get("communication") or None,
            "overall_feedback": record.get("overall_feedback"),
            "recruitment_status": record.get("recruitment_status"),
            "feedback": record.get("feedback"),
            "final_status": record.get("final_status"),
        }
        supabase.table("candidates").insert(payload).execute()
        inserted.append(record)

    return {"inserted": inserted, "skipped": skipped, "warnings": warnings}
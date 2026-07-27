import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from app.validation import validate_candidate, normalize_phone, is_duplicate
from app.google_sheets import fetch_all_candidates_from_sheets
from app.queue import enqueue_pending_candidates
from app.status import append_conversation_turn
from app.phone_utils import to_e164_india
from agent.send_functions import send_text_message, is_within_reply_window, MessagingConfigError, MessageSendError

load_dotenv()

app = FastAPI()

# Allows the dashboard (running on a different port, e.g. localhost:5173)
# to call this API directly from the browser. Restrict allow_origins to
# real deployed dashboard URL(s) once this goes to production -- "*" is
# fine for local dev only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
            "status": "pending",
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


@app.post("/trigger-sync")
async def trigger_sync():
    """
    Manual "sync now" endpoint for the dashboard's trigger button.
    Does what /sync-sheets does (pull new candidates from Google Sheets
    into Supabase), then immediately enqueues any pending candidates for
    sending instead of waiting for Celery Beat's next periodic tick
    (which still runs independently as the automatic safety net).
    """
    sync_result = await sync_sheets()

    try:
        enqueue_pending_candidates()
        enqueue_status = "ok"
    except Exception as e:
        enqueue_status = f"error: {str(e)}"

    return {**sync_result, "enqueue_status": enqueue_status}


class SendMessageRequest(BaseModel):
    candidate_id: str
    message: str


@app.post("/send-message")
async def send_message(payload: SendMessageRequest):
    """
    Lets a human (HR/recruiter) send a free-form WhatsApp message to a
    candidate directly from the dashboard, instead of only being able to
    watch the automated conversation. Respects WhatsApp's 24-hour
    free-form reply window -- outside that window, only approved
    templates can be sent, and this endpoint will correctly refuse
    rather than silently fail or send something that Twilio would
    reject anyway.
    """
    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    result = (
        supabase.table("candidates")
        .select("*")
        .eq("id", payload.candidate_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    candidate = result.data[0]

    last_inbound = candidate.get("last_candidate_message_at")
    if last_inbound:
        last_inbound_dt = datetime.fromisoformat(last_inbound.replace("Z", "+00:00"))
        if not is_within_reply_window(last_inbound_dt):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This candidate is outside WhatsApp's 24-hour free-form reply "
                    "window. Only an approved template can be sent now, not a "
                    "free-form message."
                ),
            )
    else:
        raise HTTPException(
            status_code=409,
            detail="This candidate has never replied, so no free-form reply window is open yet.",
        )

    try:
        twilio_sid = send_text_message(to=to_e164_india(candidate["phone"]), body=payload.message)
    except (MessagingConfigError, MessageSendError) as exc:
        raise HTTPException(status_code=502, detail=f"Failed to send message: {exc}")

    append_conversation_turn(payload.candidate_id, candidate, "assistant", payload.message)

    return {"status": "sent", "twilio_message_sid": twilio_sid}


@app.get("/candidates/{candidate_id}/resume-url")
def get_resume_url(candidate_id: str):
    """
    Generates a short-lived (1 hour) presigned S3 download URL for a
    candidate's resume. Must happen server-side, never in the browser --
    generating a presigned URL requires the AWS secret access key, which
    must never be exposed to the dashboard's frontend code.
    """
    result = supabase.table("candidates").select("resume_url").eq("id", candidate_id).execute()
    if not result.data or not result.data[0].get("resume_url"):
        raise HTTPException(status_code=404, detail="No resume on file for this candidate.")

    from app.resume_storage import generate_resume_download_url
    url = generate_resume_download_url(result.data[0]["resume_url"])
    if not url:
        raise HTTPException(status_code=502, detail="Failed to generate a download link.")
    return {"url": url}

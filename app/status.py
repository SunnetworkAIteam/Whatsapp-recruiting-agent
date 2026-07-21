import os
from datetime import datetime, timezone
from typing import Optional
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

VALID_STATUSES = [
    "pending", "messaged", "follow_up_sent", "final_reminder_sent",
    "awaiting_interview", "replied", "qualified",
    "escalated", "booked", "declined"
]

ALLOWED_TRANSITIONS = {
    "pending": ["messaged", "declined"],
    # "escalated" is reachable from every active pre-reply state, not just
    # "replied" -- a candidate can trigger an escalation (e.g. asking
    # about visa/legal topics) on their very first reply, before ever
    # normally transitioning to "replied". Missing this caused a real
    # ValueError crash in production testing.
    "messaged": ["follow_up_sent", "replied", "escalated", "declined"],
    "follow_up_sent": ["final_reminder_sent", "replied", "escalated", "declined"],
    "final_reminder_sent": ["replied", "escalated", "declined"],
    "replied": ["qualified", "escalated", "awaiting_interview", "declined"],
    "awaiting_interview": ["booked", "qualified", "escalated", "declined"],
    "qualified": ["booked", "declined"],
    # "replied" added so a human can resume normal bot conversation
    # after reviewing an escalation (see the dashboard's "Mark as
    # reviewed" button) -- previously status stayed "escalated" forever,
    # and the LLM layer permanently refuses to auto-reply to anyone in
    # that status, so "reviewing" an escalation had no actual effect on
    # whether the candidate could be messaged again.
    "escalated": ["booked", "declined", "replied"],
    "booked": [],       # final state
    # "declined" is NOT a true dead-end: a candidate who previously opted
    # out or was marked declined can still message again later (e.g.
    # having reconsidered), and the LLM/rule engine may legitimately
    # decide to re-engage them. Blocking every transition out of
    # "declined" caused a real crash when a candidate replied after
    # being declined and the pipeline tried to move them forward.
    "declined": ["replied", "escalated", "awaiting_interview", "qualified", "booked"],
}


# ---------------------------------------------------------------------------
# Role work details -- HR-editable via the dashboard's Settings tab
# (role_work_details table), replacing what used to be a hardcoded dict
# in agent/pipeline.py. HR previously had no way to update these values
# themselves and had to relay changes to an engineer for a code edit --
# painful if they need to adjust this every 1-2 days. A small in-process
# cache avoids hitting the database on every single outreach send.
# ---------------------------------------------------------------------------
from datetime import timedelta as _timedelta

_role_details_cache = {"data": None, "fetched_at": None}
_ROLE_DETAILS_CACHE_TTL = _timedelta(minutes=2)


def get_role_work_details(role: str) -> dict:
    """
    Return {"work_type": ..., "hours": ...} for the given role, sourced
    from the role_work_details table (editable by HR via the dashboard).
    Falls back to {"work_type": "TBD", "hours": "TBD"} for any role not
    yet configured, so an unmapped role never sends blank/broken
    template variables.
    """
    global _role_details_cache
    now = datetime.now(timezone.utc)
    cache_stale = (
        _role_details_cache["data"] is None
        or _role_details_cache["fetched_at"] is None
        or now - _role_details_cache["fetched_at"] > _ROLE_DETAILS_CACHE_TTL
    )
    if cache_stale:
        if supabase is None:
            _role_details_cache["data"] = {}
        else:
            result = supabase.table("role_work_details").select("*").execute()
            # Keyed by a normalized (trimmed, lowercased) form of the role
            # name, so a candidate's role from Sheets ingestion matches
            # regardless of casing/whitespace differences from however HR
            # typed it into the dashboard's Settings tab (e.g. "DevOps"
            # from a sheet tab vs. "devops " typed with a trailing space).
            _role_details_cache["data"] = {
                (r["role"] or "").strip().lower(): {
                    "work_type": r.get("work_type") or "TBD",
                    "hours": r.get("hours") or "TBD",
                }
                for r in (result.data or [])
            }
        _role_details_cache["fetched_at"] = now

    normalized_role = (role or "").strip().lower()
    return _role_details_cache["data"].get(normalized_role, {"work_type": "TBD", "hours": "TBD"})


# Placeholder used when a candidate's real name isn't known yet (e.g. an
# auto-created record for an unrecognized number -- see
# create_candidate_for_unknown_number below). Defined ONCE here, as the
# single source of truth for both where this placeholder is WRITTEN and
# where it needs to be RECOGNIZED and treated as "no real name" rather
# than a literal name to insert into an outbound message. Previously,
# three separate call sites each used their own ad-hoc
# `candidate.get("name") or "there"` fallback, which correctly handled a
# blank/missing name but NOT this literal placeholder string (since
# "Unknown" is a real, non-empty string, `"Unknown" or "there"` evaluates
# to "Unknown") -- causing real candidates to receive messages starting
# "Hi Unknown, this is Nisha...". Fixed once, here, rather than requiring
# every future call site to remember to special-case this string too.
UNASSIGNED_NAME_PLACEHOLDER = "Unknown"


def get_safe_display_name(candidate: dict, fallback: str = "there") -> str:
    """
    Return a candidate's real name for use in an outbound message, or
    `fallback` if we don't actually have one -- treating both a
    blank/missing name AND the literal UNASSIGNED_NAME_PLACEHOLDER
    ("Unknown") as "no real name", so neither ever gets sent to the
    candidate as if it were their actual name.
    """
    name = (candidate.get("name") or "").strip()
    if not name or name.lower() == UNASSIGNED_NAME_PLACEHOLDER.lower():
        return fallback
    return name


def append_conversation_turn(candidate_id: str, candidate: dict, role: str, content: str) -> list:
    """
    Append a single turn (role='user' or 'assistant') to a candidate's
    conversation_history and persist it. Used both for LLM-generated
    replies (already handled elsewhere) and for automated template
    sends (initial outreach, follow-ups) -- those previously never
    appeared in conversation_history at all, which meant any candidate
    who'd only received outbound messages but hadn't replied yet was
    completely invisible in the dashboard's Messages tab.
    """
    history = (candidate.get("conversation_history") or []) + [{"role": role, "content": content}]
    update_candidate_metadata(candidate_id, {"conversation_history": history})
    return history


def record_unmatched_message(phone: str, body: str) -> None:
    """
    Store an inbound WhatsApp message that didn't match any known
    candidate record, so it's visible in the dashboard instead of being
    silently dropped (see agent/pipeline.py's handle_candidate_reply --
    previously this case only logged a warning and discarded the
    message entirely).

    Kept as a lightweight audit log even though unmatched numbers are
    now also turned into real candidate records (see
    create_candidate_for_unknown_number below) -- this table is a
    permanent record of "someone messaged us who we had no record of",
    independent of whatever happens to the candidate record afterward.
    """
    if supabase is None:
        return
    supabase.table("unmatched_messages").insert({"phone": phone, "body": body}).execute()


def create_candidate_for_unknown_number(normalized_phone: str) -> dict:
    """
    Auto-creates a minimal candidate record for a phone number that
    messaged the business WhatsApp number but doesn't match any existing
    candidate. This lets the message flow through the exact same
    pipeline as any known candidate (rule engine, LLM reply,
    conversation_history) -- so it shows up in the dashboard's Messages
    tab as a normal conversation, and the agent can actually reply,
    rather than being stuck in a separate "unrecognized" dead end with
    no response.

    Uses role=None (shows as "Unspecified" in the dashboard) since we
    genuinely don't know which role this person is interested in --
    a human can fill this in once the conversation clarifies it.
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")
    now_ts = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("candidates")
        .insert({
            "name": UNASSIGNED_NAME_PLACEHOLDER,  # `name` is NOT NULL in the schema; dashboard displays phone number instead
            "phone": normalized_phone,
            "role": "Unspecified",
            # "replied" (not "messaged") -- they just messaged US first,
            # so this is genuinely an ongoing conversation, not a fresh
            # cold-outreach candidate.
            "status": "replied",
            "platform": "whatsapp",
            "source": "Inbound (unrecognized number)",
            # Also set first_outreach_sent_at -- the rule engine (see
            # agent/rule_engine.py) treats first_outreach_sent_at is None
            # as "this candidate has never received outreach, send the
            # cold JOB_OUTREACH template" regardless of status. Since
            # this person just messaged US first, we don't owe them a
            # cold outreach; setting this now correctly routes their
            # message through the normal conversational LLM path instead.
            "first_outreach_sent_at": now_ts,
        })
        .execute()
    )
    return result.data[0]


def append_conversation_turn(candidate_id: str, candidate: dict, role: str, content: str) -> list:
    """
    Append a single turn (role='user' or 'assistant') to a candidate's
    conversation_history and persist it. Used both for LLM-generated
    replies (already handled elsewhere) and for automated template
    sends (initial outreach, follow-ups) -- those previously never
    appeared in conversation_history at all, which meant any candidate
    who'd only received outbound messages but hadn't replied yet was
    completely invisible in the dashboard's Messages tab.
    """
    history = (candidate.get("conversation_history") or []) + [{"role": role, "content": content}]
    update_candidate_metadata(candidate_id, {"conversation_history": history})
    return history


def record_unmatched_message(phone: str, body: str) -> None:
    """
    Store an inbound WhatsApp message that didn't match any known
    candidate record, so it's visible in the dashboard instead of being
    silently dropped (see agent/pipeline.py's handle_candidate_reply --
    previously this case only logged a warning and discarded the
    message entirely).
    """
    if supabase is None:
        return
    supabase.table("unmatched_messages").insert({"phone": phone, "body": body}).execute()


def transition_status(candidate_id: int, new_status: str):
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")
    result = supabase.table("candidates").select("status").eq("id", candidate_id).execute()
    if not hasattr(result, "data") or not result.data:
        raise ValueError(f"Candidate {candidate_id} not found")
    current_status = result.data[0]["status"]

    if new_status == current_status:
        return f"Candidate {candidate_id} already in status {current_status}; no transition needed"

    if new_status not in ALLOWED_TRANSITIONS.get(current_status, []):
        raise ValueError(f"Cannot transition from {current_status} to {new_status}")
    supabase.table("candidates").update({"status": new_status}).eq("id", candidate_id).execute()
    return f"Candidate {candidate_id} transitioned from {current_status} to {new_status}"


def get_candidate_by_phone(phone: str) -> Optional[dict]:
    """
    Look up a candidate by their normalized phone number.
    Used by agent/pipeline.py to resolve a candidate_id from an inbound
    WhatsApp message before calling transition_status().
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")
    result = supabase.table("candidates").select("*").eq("phone", phone).limit(1).execute()
    return result.data[0] if result.data else None


def record_inbound_message(candidate_id: int, received_at: Optional[datetime] = None) -> dict:
    """
    Stamp the timestamp of the candidate's latest inbound message.
    Used by agent/rule_engine.py's check_reply_window() to enforce
    WhatsApp's 24-hour free-form messaging window.
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")
    timestamp = (received_at or datetime.now(timezone.utc)).isoformat()
    result = (
        supabase.table("candidates")
        .update({"last_candidate_message_at": timestamp})
        .eq("id", candidate_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def update_candidate_metadata(candidate_id: int, updates: dict) -> dict:
    """
    Update arbitrary metadata columns on a candidate row (e.g.
    last_template_sent_at, followup_1_sent_at) without going through the
    status state machine. Any datetime values are serialized to ISO format
    automatically.

    Used by agent/pipeline.py after a template/text send, and by the
    follow-up scheduler after dispatching a follow-up message.
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")

    payload = {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in updates.items()
    }

    result = (
        supabase.table("candidates")
        .update(payload)
        .eq("id", candidate_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def get_candidates_pending_followup() -> list[dict]:
    """
    Return candidates the follow-up scheduler should evaluate: those in
    "messaged" status (Track A -- never replied) or "replied" status
    (Track B -- engaged but hasn't scheduled an interview yet). Terminal
    states (booked, declined, escalated) are excluded since no further
    outbound follow-up makes sense there.

    Filtering on timing (24h since last contact, 9PM IST window) and on
    which track a candidate falls into happens in the scheduler itself,
    since that logic depends on the current time and per-candidate state,
    not just this DB query.
    """
    if supabase is None:
        raise RuntimeError("Supabase client not initialized")

    result = (
        supabase.table("candidates")
        .select("*")
        .in_("status", ["messaged", "follow_up_sent", "replied"])
        .execute()
    )
    return result.data or []
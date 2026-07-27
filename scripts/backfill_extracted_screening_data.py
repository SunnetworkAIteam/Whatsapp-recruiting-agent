"""
scripts/backfill_extracted_screening_data.py

One-time backfill: candidates who had a full conversation BEFORE the
structured-extraction feature existed never got their location, CTC,
notice period, or experience saved as separate fields -- even though
they may have stated all of this in their conversation_history. This
re-reads each such candidate's existing conversation and asks the LLM
to extract the same 5 fields retroactively, in one pass per candidate
(not a live conversational turn -- no message is sent, no status
changes, this is read-only analysis of already-stored history).

Only processes candidates where ALL 5 extracted_* fields are still
null, so it never overwrites anything already correctly captured by
the live extraction going forward. Safe to run multiple times.
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm_layer import _call_anthropic_api

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

EXTRACTION_PROMPT = """You are analyzing a WhatsApp recruiting conversation transcript between an AI recruiting assistant and a job candidate.

Extract ONLY the following details, if the candidate actually stated them anywhere in the conversation. Report each value in whatever wording/format the candidate actually used -- do not reformat, convert, guess, or normalize it.

Return ONLY a valid JSON object with these exact field names. Omit any field the candidate never actually stated -- do not include it with an empty string or null, just leave the key out entirely.

{
    "extracted_location": "...",
    "extracted_current_ctc": "...",
    "extracted_expected_ctc": "...",
    "extracted_notice_period": "...",
    "extracted_experience": "..."
}

Return NOTHING except the JSON object -- no preamble, no explanation."""


def _build_transcript(conversation_history: list) -> str:
    lines = []
    for turn in conversation_history:
        role = "Candidate" if turn.get("role") == "user" else "Recruiter Bot"
        content = turn.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL or SUPABASE_KEY not set in .env")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching candidates with no extracted screening data yet...")
    result = supabase.table("candidates").select(
        "id, conversation_history, extracted_location, extracted_current_ctc, "
        "extracted_expected_ctc, extracted_notice_period, extracted_experience"
    ).execute()

    candidates_to_process = [
        r for r in result.data
        if r.get("conversation_history")
        and not any(r.get(f) for f in (
            "extracted_location", "extracted_current_ctc", "extracted_expected_ctc",
            "extracted_notice_period", "extracted_experience",
        ))
    ]
    print(f"Found {len(candidates_to_process)} candidate(s) to process.\n")

    updated_count = 0
    for candidate in candidates_to_process:
        transcript = _build_transcript(candidate["conversation_history"])
        if not transcript.strip():
            continue

        try:
            raw = _call_anthropic_api(
                messages=[{"role": "user", "content": transcript}],
                api_key=None,
                system_prompt=EXTRACTION_PROMPT,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            extracted = json.loads(cleaned.strip())
        except Exception as exc:
            print(f"  SKIP {candidate['id'][:8]} -- extraction failed: {exc}")
            continue

        updates = {k: v for k, v in extracted.items() if v}
        if not updates:
            print(f"  SKIP {candidate['id'][:8]} -- nothing found in transcript")
            continue

        from datetime import datetime, timezone
        updates["extracted_details_updated_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("candidates").update(updates).eq("id", candidate["id"]).execute()

        history_rows = [
            {"candidate_id": candidate["id"], "field_name": field, "value": value}
            for field, value in updates.items()
            if field != "extracted_details_updated_at"
        ]
        if history_rows:
            supabase.table("screening_field_history").insert(history_rows).execute()

        print(f"  OK   {candidate['id'][:8]} -- captured {len(history_rows)} field(s)")
        updated_count += 1

    print(f"\nDone. Updated {updated_count} of {len(candidates_to_process)} candidate(s).")


if __name__ == "__main__":
    main()
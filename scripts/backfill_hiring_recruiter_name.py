"""
scripts/backfill_hiring_recruiter_name.py

One-time backfill: candidates synced BEFORE the header-alias fix (see
app/google_sheets.py's HEADER_ALIASES -- the real production sheet uses
"Hiring Recruiter" with no "Name" suffix, which wasn't mapped) were
inserted with hiring_recruiter_name left blank. This re-reads the real
Google Sheets (now parsed correctly) and updates ONLY the
hiring_recruiter_name field for existing candidates, matched by
normalized phone number -- nothing else about their record is touched.

Safe to run multiple times: candidates who already have a
hiring_recruiter_name set are left alone, only ones still blank are
updated.

MUST be run on the server (or wherever GOOGLE_DRIVE_FOLDER_ID and the
Google service account credentials point at the REAL production Drive
folder) -- running this against a different folder would backfill
nothing or the wrong data entirely.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.google_sheets import fetch_all_candidates_from_sheets
from app.validation import normalize_phone

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL or SUPABASE_KEY not set in .env")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching current candidates missing hiring_recruiter_name...")
    result = supabase.table("candidates").select("id, phone, hiring_recruiter_name").execute()
    missing_by_phone = {
        r["phone"]: r["id"] for r in result.data if not r.get("hiring_recruiter_name")
    }
    print(f"Found {len(missing_by_phone)} candidate(s) missing hiring_recruiter_name.")

    if not missing_by_phone:
        print("Nothing to do.")
        return

    print("Re-reading Google Sheets (now with the fixed header aliases)...")
    records, warnings = fetch_all_candidates_from_sheets()
    for w in warnings:
        print(f"  WARNING: {w}")

    updated = 0
    skipped_no_recruiter_in_sheet = 0
    for record in records:
        raw_phone = record.get("phone", "")
        normalized = normalize_phone(raw_phone)
        recruiter_name = (record.get("hiring_recruiter_name") or "").strip()

        if normalized not in missing_by_phone:
            continue  # not one of the candidates we need to fix, or already has a value

        if not recruiter_name:
            skipped_no_recruiter_in_sheet += 1
            continue  # sheet itself has no recruiter name for this row either

        candidate_id = missing_by_phone[normalized]
        supabase.table("candidates").update(
            {"hiring_recruiter_name": recruiter_name}
        ).eq("id", candidate_id).execute()
        updated += 1

    print(f"\nUpdated {updated} candidate(s) with their real hiring_recruiter_name.")
    print(f"Skipped {skipped_no_recruiter_in_sheet} row(s) where the sheet itself had no recruiter name.")
    still_missing = len(missing_by_phone) - updated
    if still_missing > 0:
        print(f"{still_missing} candidate(s) still missing -- likely no longer present in the sheet, or a phone number mismatch.")


if __name__ == "__main__":
    main()
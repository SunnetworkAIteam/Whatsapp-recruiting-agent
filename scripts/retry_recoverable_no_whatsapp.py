"""
scripts/retry_recoverable_no_whatsapp.py

Retries outreach for candidates flagged "no_whatsapp" due to error
codes that are likely recoverable (63049 -- Meta blocking delivery,
often tied to Business Verification status; 63032 -- a vaguer
WhatsApp-side limitation), as opposed to 63024 (genuinely not a
WhatsApp number), which is left untouched since retrying it would
never succeed.

Safe to run repeatedly -- e.g. once after Meta Business Verification
completes, to recover anyone who was blocked before that finished.
Uses the same staggered (2-second gap) sending as a normal sync, via
app.queue.enqueue_pending_candidates(), so this doesn't send a burst
of messages all at once either.

Usage:
    python scripts/retry_recoverable_no_whatsapp.py
    python scripts/retry_recoverable_no_whatsapp.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.queue import retry_recoverable_no_whatsapp_candidates

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def main():
    parser = argparse.ArgumentParser(
        description="Retry candidates flagged no_whatsapp due to recoverable Twilio/Meta errors."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show how many candidates would be retried, without actually resetting/sending anything.",
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL or SUPABASE_KEY not set in .env")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if args.dry_run:
        from app.queue import RECOVERABLE_NO_WHATSAPP_ERROR_CODES
        result = (
            supabase.table("candidates")
            .select("id")
            .eq("platform", "no_whatsapp")
            .in_("delivery_error_code", RECOVERABLE_NO_WHATSAPP_ERROR_CODES)
            .execute()
        )
        print(f"Found {len(result.data or [])} candidate(s) with a recoverable error code.")
        print("(dry run -- nothing was reset or sent)")
        return

    import app.queue as queue_module
    queue_module.supabase = supabase
    count = retry_recoverable_no_whatsapp_candidates()
    print(f"Retried {count} candidate(s). Sends will complete over the next few minutes via Celery.")


if __name__ == "__main__":
    main()
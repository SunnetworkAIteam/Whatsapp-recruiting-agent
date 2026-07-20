"""
scripts/catch_up_missed_messages.py

Recovery script: fetches inbound WhatsApp messages from Twilio and feeds
any not-yet-processed ones through the normal agent pipeline
(agent.pipeline.handle_candidate_reply) -- the same function the live
webhook calls -- so nothing is silently lost.

This covers two real failure modes, not just "the webhook was down":
  1. Genuine downtime (server stopped/restarted, deploy, crash).
  2. A request that started processing but got interrupted partway
     through (e.g. the process was killed/restarted mid-request) --
     this looks identical from the outside to "never arrived": the
     inbound timestamp gets recorded, but the reply and
     conversation_history never get saved. This actually happened once
     in testing (a candidate's reply was silently incomplete after a
     webhook restart landed mid-request).

Twilio does NOT automatically redeliver webhook calls once its retry
window has passed, so nothing else catches either case on its own.

SAFE TO RUN AUTOMATICALLY / REPEATEDLY: processed message SIDs are
recorded in a local state file (CATCHUP_STATE_FILE) and skipped on
future runs, so running this with a wide time window (as
scripts/start_all.sh now does on every startup) will not re-send
replies for messages that were already handled correctly. Only
messages whose SID has never been successfully processed are acted on.

Usage:
    python scripts/catch_up_missed_messages.py --hours 48
    python scripts/catch_up_missed_messages.py --hours 6 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webhook.schemas import TwilioInboundMessage
from agent.pipeline import handle_candidate_reply

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATCHUP_STATE_FILE = os.path.join(REPO_ROOT, ".catchup_processed_sids.json")


def _load_processed_sids() -> set:
    if not os.path.exists(CATCHUP_STATE_FILE):
        return set()
    try:
        with open(CATCHUP_STATE_FILE, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_processed_sids(sids: set) -> None:
    with open(CATCHUP_STATE_FILE, "w") as f:
        json.dump(sorted(sids), f)


def main():
    parser = argparse.ArgumentParser(description="Catch up on missed/incomplete inbound WhatsApp messages.")
    parser.add_argument(
        "--hours", type=float, default=48,
        help="How many hours back to look for inbound messages (default: 48).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List the messages that would be processed, without actually processing them.",
    )
    args = parser.parse_args()

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    business_number = os.environ.get("TWILIO_WHATSAPP_NUMBER")

    if not account_sid or not auth_token or not business_number:
        print("ERROR: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_WHATSAPP_NUMBER not set in .env")
        sys.exit(1)

    client = Client(account_sid, auth_token)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    business_whatsapp = business_number if business_number.startswith("whatsapp:") else f"whatsapp:{business_number}"

    processed_sids = _load_processed_sids()

    print(f"Fetching messages sent to {business_whatsapp} since {cutoff.isoformat()}...")
    messages = client.messages.list(to=business_whatsapp, date_sent_after=cutoff)

    inbound = [m for m in messages if m.direction == "inbound"]
    inbound.sort(key=lambda m: m.date_sent)

    new_inbound = [m for m in inbound if m.sid not in processed_sids]

    print(f"Found {len(inbound)} inbound message(s) in window; {len(new_inbound)} not yet processed.\n")

    if not new_inbound:
        print("Nothing new to do.")
        return

    for m in new_inbound:
        print(f"  {m.date_sent} | from={m.from_} | body={m.body[:60]!r}")

    if args.dry_run:
        print("\n(dry run -- nothing was processed, state file unchanged)")
        return

    print("\nProcessing...")
    for m in new_inbound:
        message = TwilioInboundMessage(
            MessageSid=m.sid,
            From=m.from_,
            To=m.to,
            Body=m.body or "",
        )
        try:
            result = handle_candidate_reply(message)
            print(f"  OK   sid={m.sid} -> {result.get('status')} ({result.get('reason', '')[:60]})")
            processed_sids.add(m.sid)
        except Exception as exc:
            print(f"  FAIL sid={m.sid} -> {exc}")

    _save_processed_sids(processed_sids)
    print("\nDone.")


if __name__ == "__main__":
    main()

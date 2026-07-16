"""
scripts/catch_up_missed_messages.py

One-off recovery script: fetches inbound WhatsApp messages from Twilio
that arrived while the webhook server was down (e.g. between stopping
and restarting scripts/start_all.sh), and feeds each one through the
normal agent pipeline (agent.pipeline.handle_candidate_reply) -- the
same function the live webhook calls -- so nothing is silently lost.

Twilio does NOT automatically redeliver webhook calls once its retry
window has passed; messages that arrived during downtime just sit in
Twilio's own message log unless something goes and processes them.
This script is that "something."

Usage:
    python scripts/catch_up_missed_messages.py --hours 6

    --hours: how far back to look for inbound messages (default: 6).
    Pick a window that comfortably covers your actual downtime -- pulling
    too wide a window risks reprocessing messages that were already
    handled live (this script does not check the idempotency store used
    by the running webhook, since that store is in-memory and doesn't
    persist across restarts anyway).

Safe to re-run, but be aware: replies to a candidate will be sent again
if you re-run over the same window (e.g. an LLM reply, or a status
transition already applied once). Prefer a tight, accurate window over
a wide one.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webhook.schemas import TwilioInboundMessage
from agent.pipeline import handle_candidate_reply


def main():
    parser = argparse.ArgumentParser(description="Catch up on missed inbound WhatsApp messages.")
    parser.add_argument(
        "--hours", type=float, default=6,
        help="How many hours back to look for inbound messages (default: 6).",
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

    print(f"Fetching messages sent to {business_whatsapp} since {cutoff.isoformat()}...")

    messages = client.messages.list(to=business_whatsapp, date_sent_after=cutoff)

    inbound = [m for m in messages if m.direction == "inbound"]
    inbound.sort(key=lambda m: m.date_sent)

    print(f"Found {len(inbound)} inbound message(s) in this window.\n")

    if not inbound:
        print("Nothing to do.")
        return

    for m in inbound:
        print(f"  {m.date_sent} | from={m.from_} | body={m.body[:60]!r}")

    if args.dry_run:
        print("\n(dry run -- nothing was processed)")
        return

    print("\nProcessing...")
    for m in inbound:
        message = TwilioInboundMessage(
            MessageSid=m.sid,
            From=m.from_,
            To=m.to,
            Body=m.body or "",
        )
        try:
            result = handle_candidate_reply(message)
            print(f"  OK   sid={m.sid} -> {result.get('status')} ({result.get('reason', '')[:60]})")
        except Exception as exc:
            print(f"  FAIL sid={m.sid} -> {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()

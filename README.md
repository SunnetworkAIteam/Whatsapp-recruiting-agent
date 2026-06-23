# WhatsApp Recruiting Agent

An automated candidate outreach pipeline. HR uploads a candidate list, the system sends a WhatsApp first-contact message, and a hybrid rule-based + LLM-powered **WhatsApp agent** handles replies, qualifies candidates, and escalates to a human when needed — all visible live on an HR dashboard.

## How it works

1. **HR uploads an Excel file** (Name, number, role)
2. System **parses and validates** with pandas (dedup, phone format checks) and saves candidates to the DB with status `pending`
3. A rate-limited **queue** sends each candidate a Meta-approved WhatsApp **template message** via Twilio
4. When a candidate **replies**, a webhook fires and the message passes through:
   - A **rule engine** first — checking for opt-out (STOP), escalation triggers (salary, visa, complaints), and the 24-hour reply window
   - If no rule trips, an **LLM layer** generates a reply and a structured status update, including a confidence score
   - Low-confidence or rule-flagged messages are routed to a human instead of being auto-sent
5. **HR dashboard** shows the live pipeline: `pending → messaged → replied → qualified → escalated → booked / declined`

## Status flow

```
pending → messaged → replied → qualified → booked
                   ↘ escalated → (human resolves) → booked / declined
                   ↘ declined (opt-out at any point)
```

## WhatsApp constraints this system respects

- **First message must use a Meta-approved template** — free-form text isn't allowed for first contact
- **Free-form replies only work within a 24-hour window** after the candidate replies; outside that window, a new template message is required

## Tech stack

- **Backend:** Python (FastAPI), pandas
- **Queue:** Celery / RQ with Redis as broker
- **Database:** PostgreSQL
- **Messaging:** Twilio WhatsApp API
- **LLM:** Anthropic API (Claude)
- **Dashboard:** _(fill in — React, etc.)_

## Project structure

```
whatsapp-recruiting-agent/
├── ingestion/        # Excel upload, parsing, validation, dedup (Person A)
├── infra/             # DB schema, queue/worker setup (Person A)
├── agent/             # Rule engine + LLM layer (Person B)
├── webhook/           # Inbound webhook, idempotency, orchestration (Person C)
├── dashboard/         # HR live pipeline view (Person C)
├── tests/
├── .env.example
├── CONTRIBUTING.md    # Team workflow — read this first
└── README.md
```

## Getting started

This project is split so most contributors **don't need Docker or a database running locally** to write and test their part. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for:
- How to set up your environment
- What you can test on your own laptop vs. what the integration lead tests
- The `.env.example` config template
- Function-level contracts between each person's piece

## Status

In active development. See GitHub Issues and Milestones for current progress.

## Credentials & security

- Never commit a `.env` file with real values — only `.env.example` with placeholders is tracked in git
- Twilio and production DB credentials are held by the integration lead only
- LLM API keys used by contributors are scoped/spend-limited project keys, not personal or production keys

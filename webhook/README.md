# Candidate Reply Webhook (WhatsApp / Twilio)

Receives inbound candidate WhatsApp replies via Twilio's webhook, with
idempotency so retried/duplicate deliveries never get processed twice.

## Structure

```
webhook/
  app.py          - FastAPI app + POST /webhooks/whatsapp/inbound endpoint
  schemas.py      - TwilioInboundMessage pydantic model
  security.py     - Twilio webhook signature verification
  idempotency.py  - Thread-safe idempotency store (in-memory, TTL-based)
  orchestrator.py - Handoff point to the agent/ (rule engine + LLM) module
tests/
  test_webhook.py - pytest tests for idempotency + signature verification
requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

## Run the tests

```bash
python -m pytest tests/test_webhook.py -v
```

## Run the server

```bash
python -m uvicorn webhook.app:app --reload
```

Signature verification is on by default. For local dev without a real
Twilio-signed URL, disable it via an env var before starting the server:

```bash
# PowerShell
$env:TWILIO_VALIDATE_SIGNATURE = "false"
python -m uvicorn webhook.app:app --reload
```

```bash
# bash
export TWILIO_VALIDATE_SIGNATURE=false
python -m uvicorn webhook.app:app --reload
```

## How idempotency works

Twilio includes a unique `MessageSid` on every inbound message and retries
delivery (resending the same `MessageSid`) if your endpoint is slow or
returns a non-2xx status. This webhook:

1. Verifies the request came from Twilio (signature check).
2. Atomically reserves the `MessageSid` in `IdempotencyStore` before doing
   any real work (`save_if_absent`), so two racing retries can't both slip
   through.
3. If the key was already seen, returns immediately without re-running the
   agent pipeline.
4. Otherwise hands the parsed message to `orchestrator.process_inbound_message`,
   which is where Person B's rule-engine/LLM pipeline plugs in via
   `set_message_processor(...)`.

**Production note:** the store is in-memory, which is fine for a single
process/tests. Once Celery/RQ workers run as separate processes, back this
with Redis (`SETNX`) or a Postgres unique constraint on `message_sid`
instead, so idempotency holds across the whole fleet and survives restarts.

## Integrating the agent module

```python
# in the app's startup code, once agent/ exists:
from webhook.orchestrator import set_message_processor
from agent.pipeline import handle_candidate_reply

set_message_processor(handle_candidate_reply)
```

`handle_candidate_reply` should accept a `TwilioInboundMessage` and return
a dict describing the outcome (status update, confidence score, etc).

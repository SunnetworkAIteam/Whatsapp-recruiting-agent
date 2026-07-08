# Webhook Orchestration (Python)

Implements `handle_candidate_reply` — the function `webhook/orchestrator.py`
wires up via `set_message_processor`. This connects the webhook to the rule
engine, the LLM layer, and the send step, in Python, matching your existing
`app/`, `agent/`, and `webhook/` structure.

## ⚠️ This was built without your real files

No files were uploaded to the conversation this was built in, so
`agent/pipeline.py` was written against the **names and flow you described**
(`TwilioInboundMessage`, `gate_message()`, `PASS_TO_LLM`, `process_message()`,
`transition_status()`) — not the actual signatures in your repo. Every
assumption is marked with `# ASSUMPTION:` comments directly in
`agent/pipeline.py`. The most important ones:

1. `TwilioInboundMessage` is assumed to have `.from_` and `.body` fields
   (matching Twilio's raw `From`/`Body` webhook payload). If your schema
   names them differently, update the two lines in `handle_candidate_reply`
   that read `message.from_` and `message.body`.
2. `gate_message(text)` is assumed to return something directly comparable
   to `PASS_TO_LLM`, with a `.reason` attribute when it doesn't pass.
3. `process_message(candidate_id, text)` is assumed to return a plain string
   reply. If it returns a dict/object instead, adjust the one line that
   assigns `reply_text`.
4. `transition_status(candidate_id, status)` is assumed to take plain
   strings like `"escalated"` / `"replied"`. Swap for your real status
   constants/enum.
5. There's no dedicated Python "send functions" file mentioned yet, so a
   placeholder `send_reply()` (with a Twilio mock-mode fallback) lives
   directly in `agent/pipeline.py`. Point me at your real send module and
   I'll wire against that instead of this placeholder.

**Once you share the real `webhook/schemas.py`, `agent/rule_engine.py`,
`agent/llm_layer.py`, and `app/status.py`**, send them over and I'll update
`agent/pipeline.py` to match exactly, and delete the placeholder/stub files
below.

## What to actually drop into your repo

Only **`agent/pipeline.py`** is meant for your real repo. Everything else
here is scaffolding so it can be tested standalone:

| File | Purpose |
|---|---|
| `agent/pipeline.py` | **The real deliverable.** Drop this into your repo at `agent/pipeline.py`. |
| `_test_stubs.py` | Fake minimal versions of your real modules, used only so tests can run without your actual files. **Delete this once integrated.** |
| `agent/rule_engine.py`, `agent/llm_layer.py`, `app/status.py`, `webhook/schemas.py` | Thin re-exports of `_test_stubs.py`, present only so `agent/pipeline.py`'s imports resolve for local testing. **Delete these — your repo already has the real versions.** |
| `test_pipeline.py` | Proves the wiring and escalation gate work. Should keep passing once you swap in the real modules, since it only calls `handle_candidate_reply()` and checks its output. |

## Flow

```
TwilioInboundMessage
        │
        ▼
handle_candidate_reply()          (agent/pipeline.py)
        │
        ▼
gate_message(text)                (agent/rule_engine.py)
        │
   ┌────┴─────┐
   │          │
ESCALATE   PASS_TO_LLM
   │          │
   ▼          ▼
transition_status()   process_message()     (agent/llm_layer.py)
  "escalated"                │
   STOP — no LLM             ▼
   call, no send        send_reply()
                              │
                              ▼
                    transition_status()
                         "replied"
```

## The escalation guarantee

Same guarantee as the JS version built earlier in this conversation, ported
to Python:

1. `gate_message` is the only thing that decides `PASS_TO_LLM` vs escalate —
   deterministic keyword checks, not fuzzy/model-based, so it's auditable.
2. `handle_candidate_reply` `return`s immediately on escalation — the LLM
   call and `send_reply` call are unreachable code on that branch, not just
   skipped by a flag.
3. `transition_status(candidate_id, "escalated")` is the one place that
   writes the escalated state to Supabase, so it's the one place to audit.

## Run the tests

```bash
pip install -r requirements.txt
python test_pipeline.py
```

No real credentials needed — `send_reply()` falls back to mock mode
whenever `TWILIO_AUTH_TOKEN` isn't set in the environment.

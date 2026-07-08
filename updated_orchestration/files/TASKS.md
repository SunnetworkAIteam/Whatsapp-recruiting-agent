# Webhook Orchestration (Python) — Tasks

## 1. Wire the webhook to the rule engine, LLM layer, and send step
- [x] `handle_candidate_reply(message)` implemented in `agent/pipeline.py`,
      matching the `set_message_processor` placeholder already in
      `webhook/orchestrator.py`
- [x] Message text passed to `agent.rule_engine.gate_message()`
- [x] If `gate_message()` returns `PASS_TO_LLM`, passed to
      `agent.llm_layer.process_message()`
- [x] Reply sent back out via `send_reply()` (placeholder — swap for real
      send module once shared)
- [x] Outcome recorded via `app.status.transition_status()`
- [x] Returns a dict describing the outcome

## 2. Escalated conversations marked correctly and never auto-sent
- [x] If `gate_message()` does not return `PASS_TO_LLM`, `handle_candidate_reply`
      returns immediately — the LLM call and `send_reply()` call are
      unreachable on that branch
- [x] `transition_status(candidate_id, "escalated")` called before returning
- [x] Verified with `test_pipeline.py`: normal message → `process_message()`
      called, `send_reply()` called, status → `"replied"`; trigger message →
      neither called, status → `"escalated"`, never `"replied"`

## Blocked on / needs from the team
- [ ] Real `webhook/schemas.py` — to confirm `TwilioInboundMessage`'s actual
      field names (assumed `.from_` / `.body`)
- [ ] Real `agent/rule_engine.py` — to confirm `gate_message()`'s return type
      and how `PASS_TO_LLM` should actually be compared
- [ ] Real `agent/llm_layer.py` — to confirm `process_message()`'s return
      shape (assumed plain string)
- [ ] Real `app/status.py` — to confirm `transition_status()`'s status value
      constants and Supabase write behavior
- [ ] Wherever the real Twilio/WhatsApp send function lives — `send_reply()`
      in `agent/pipeline.py` is a placeholder until then

## Notes
- Once the real files are shared, delete `_test_stubs.py` and the four
  placeholder re-export files (`agent/rule_engine.py`, `agent/llm_layer.py`,
  `app/status.py`, `webhook/schemas.py`) from this folder — your repo
  already has the real ones.
- `agent/pipeline.py` is the only file meant to actually land in the repo.

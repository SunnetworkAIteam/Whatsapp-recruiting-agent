"""
agent/pipeline.py

Implements handle_candidate_reply — the function webhook/orchestrator.py's
set_message_processor() is wired up to via:

    from webhook.orchestrator import set_message_processor
    from agent.pipeline import handle_candidate_reply
    set_message_processor(handle_candidate_reply)

Flow: webhook receives a reply -> rule_engine.gate_message() -> if it passes,
llm_layer.process_message() -> send the reply back out -> transition_status()
records the outcome in Supabase.

If gate_message() does NOT return PASS_TO_LLM, this stops immediately:
no LLM call, no outbound send. The conversation is transitioned to an
escalated status instead, so it shows up for HR and nothing goes out
automatically.

=================================================================================
ASSUMPTIONS — please correct these against the real files once you share them
=================================================================================
I do not have the actual contents of webhook/schemas.py, agent/rule_engine.py,
agent/llm_layer.py, or app/status.py, only the names and flow you described.
I've made the following best-guess assumptions, all marked inline with
`# ASSUMPTION:` comments so they're easy to find and fix:

1. TwilioInboundMessage has fields `.from_` (candidate's phone number /
   candidate id) and `.body` (message text). Twilio's own webhook payload
   uses `From` and `Body`, so this is the most likely shape, but adjust if
   your schema names them differently (e.g. `.candidate_id`, `.text`).

2. gate_message(text: str) -> GateResult, where GateResult is either an enum
   or string constant compared against `PASS_TO_LLM`, and (when it does NOT
   pass) exposes a `.reason` describing why. Adjust the comparison and
   attribute access once you share agent/rule_engine.py.

3. process_message(candidate_id: str, text: str) -> str (the reply text to
   send back). If it actually returns a dict or a richer object, adjust
   `llm_result` handling below.

4. transition_status(candidate_id: str, status: str) exists in app/status.py
   and writes to Supabase. I don't know your actual status string constants
   (e.g. "escalated" vs "ESCALATED" vs an enum), so I've used plain strings
   — swap for your real constants.

5. There's no dedicated "send functions" file mentioned in this thread's
   Python context, so I've added `send_reply()` below as a placeholder with
   a mock-mode fallback (same pattern as the JS version built earlier in
   this conversation). Point me at the real send module once it exists and
   I'll wire against that instead.
=================================================================================
"""

import os

from agent import rule_engine
from agent import llm_layer
from app import status as status_module

# ASSUMPTION: webhook.schemas.TwilioInboundMessage exists; imported for type
# hints only, not redefined here.
from webhook.schemas import TwilioInboundMessage


def send_reply(candidate_id: str, text: str) -> dict:
    """
    Sends the generated reply back out to the candidate.

    MOCK MODE: if TWILIO credentials aren't set in the environment, this
    logs what would have been sent instead of making a real call. Swap the
    body of the `else` branch for your actual Twilio (or WhatsApp Cloud API)
    send call once you point me at where that already lives in your repo.
    """
    mock_mode = not os.environ.get("TWILIO_AUTH_TOKEN")

    if mock_mode:
        print(f'[MOCK SEND] Would send to {candidate_id}: "{text}"')
        return {"mock": True, "to": candidate_id, "text": text}

    # ASSUMPTION: replace with your real Twilio send call, e.g.:
    #   from twilio.rest import Client
    #   client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    #   message = client.messages.create(body=text, from_=TWILIO_NUMBER, to=candidate_id)
    #   return {"sid": message.sid}
    raise NotImplementedError(
        "Real Twilio send not wired up yet — point me at your send function and I'll connect it."
    )


def handle_candidate_reply(message: TwilioInboundMessage) -> dict:
    """
    Orchestrates a single inbound candidate reply through the full pipeline:

        webhook -> rule_engine.gate_message() -> (escalate & stop)
                                                or (llm_layer.process_message() -> send_reply())

    Returns a dict describing the outcome, e.g.:
        {"escalated": False, "reply": "..."}
        {"escalated": True, "reason": "..."}
    """
    # ASSUMPTION: field names on TwilioInboundMessage — adjust to match the
    # real schema (e.g. message.candidate_id, message.text).
    candidate_id = message.from_
    text = message.body

    gate_result = rule_engine.gate_message(text)

    # ASSUMPTION: PASS_TO_LLM is importable from agent.rule_engine and
    # gate_result is directly comparable to it (enum or string constant).
    if gate_result != rule_engine.PASS_TO_LLM:
        # Escalate and stop. No LLM call, no send — this branch is the hard
        # gate: nothing below this block executes for an escalated message.
        reason = getattr(gate_result, "reason", str(gate_result))
        status_module.transition_status(candidate_id, "escalated")
        return {"escalated": True, "reason": reason}

    # Cleared by the rule engine — safe to generate and send a reply.
    reply_text = llm_layer.process_message(candidate_id, text)

    send_reply(candidate_id, reply_text)

    status_module.transition_status(candidate_id, "replied")

    return {"escalated": False, "reply": reply_text}

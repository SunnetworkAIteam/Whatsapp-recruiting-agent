"""
Orchestration entrypoint for inbound candidate replies.

This is the seam between webhook/ (this module, owned by Person C) and
agent/ (rule engine + LLM layer, owned by Person B). The webhook's only
job is: verify the request, dedupe it, and hand the parsed message to
whatever is registered here — it doesn't know or care whether the reply
gets handled by a keyword rule or an LLM call.

In production this should enqueue onto Celery/RQ (infra/, Person A) rather
than run inline, so the webhook can return to Twilio immediately. Swap
`process_inbound_message` for `enqueue_inbound_message` once that queue
exists; the webhook code calling this function doesn't need to change.
"""

from __future__ import annotations

from typing import Callable

from .schemas import TwilioInboundMessage

# Default (placeholder) processor. Person B's agent module should provide
# the real implementation and register it via `set_message_processor`
# below, e.g. at app startup:
#
#   from webhook.orchestrator import set_message_processor
#   from agent.pipeline import handle_candidate_reply
#   set_message_processor(handle_candidate_reply)


def _default_processor(message: TwilioInboundMessage) -> dict:
    return {
        "status": "received",
        "note": "No agent pipeline registered yet — replace via set_message_processor().",
    }


MessageProcessor = Callable[[TwilioInboundMessage], dict]

_message_processor: MessageProcessor = _default_processor


def set_message_processor(processor: MessageProcessor) -> None:
    """Swap in the real rule-engine/LLM pipeline. Also used by tests to inject fakes."""
    global _message_processor
    _message_processor = processor


def reset_message_processor() -> None:
    global _message_processor
    _message_processor = _default_processor


def process_inbound_message(message: TwilioInboundMessage) -> dict:
    return _message_processor(message)

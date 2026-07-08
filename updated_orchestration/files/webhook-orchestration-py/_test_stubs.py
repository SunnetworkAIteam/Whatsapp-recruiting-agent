"""
_test_stubs.py

NOT part of the real pipeline. These are minimal stand-ins for
webhook/schemas.py, agent/rule_engine.py, agent/llm_layer.py, and
app/status.py, used only so test_pipeline.py can run and prove the
orchestration logic in agent/pipeline.py works — without needing your
actual repo files, which weren't shared in this conversation.

DELETE THIS FILE once you drop agent/pipeline.py into your real repo. The
real webhook/schemas.py, agent/rule_engine.py, agent/llm_layer.py, and
app/status.py already exist there and should be used instead.
"""

from dataclasses import dataclass


# ---- stand-in for webhook/schemas.py ---------------------------------------

@dataclass
class TwilioInboundMessage:
    from_: str
    body: str


# ---- stand-in for agent/rule_engine.py -------------------------------------

PASS_TO_LLM = "PASS_TO_LLM"


class GateResult:
    def __init__(self, value, reason=None):
        self.value = value
        self.reason = reason

    def __eq__(self, other):
        return self.value == other

    def __repr__(self):
        return f"GateResult({self.value!r})"


_ESCALATION_TRIGGERS = ["salary", "speak to a human", "lawyer", "urgent"]


def gate_message(text: str):
    normalized = (text or "").lower()
    for trigger in _ESCALATION_TRIGGERS:
        if trigger in normalized:
            return GateResult("ESCALATE", reason=f"Message matched trigger: '{trigger}'.")
    return GateResult(PASS_TO_LLM)


# ---- stand-in for agent/llm_layer.py ---------------------------------------

def process_message(candidate_id: str, text: str) -> str:
    return f'(stub reply) Thanks for your message — following up on: "{text}"'


# ---- stand-in for app/status.py --------------------------------------------

_status_log = []


def transition_status(candidate_id: str, status: str):
    _status_log.append((candidate_id, status))
    print(f"[STATUS] {candidate_id} -> {status}")


def _get_status_log():
    return list(_status_log)


def _reset_status_log():
    _status_log.clear()

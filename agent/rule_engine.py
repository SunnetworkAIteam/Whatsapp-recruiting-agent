"""
rule_engine.py

Rule engine for the WhatsApp Recruiting Agent.

This module sits BEFORE the LLM layer. Its job is to catch anything that
must be handled deterministically -- opt-outs, sensitive escalation topics,
and WhatsApp's 24-hour free-form messaging window -- so the LLM never has
to make a judgment call on these.

This module also owns template selection: it decides not only WHETHER a
template must be sent (SEND_TEMPLATE) but WHICH template should be sent.

Supported template types:
    * JOB_OUTREACH
    * NO_REPLY
    * FINAL_REMINDER
    * INTERVIEW_FOLLOW_UP

The Messaging Layer (messaging.py) only knows how to send a message once it's 
been told exactly what to send; it never decides.

Public entry point: gate_message()
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Input State Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CandidateState:
    """
    Encapsulates a candidate's messaging history and scheduling flags.
    Used by the Rule Engine to evaluate advanced multi-stage tracking workflows.
    """
    first_outreach_sent_at: Optional[datetime] = None
    last_candidate_message_at: Optional[datetime] = None
    last_template_sent_at: Optional[datetime] = None
    interview_scheduled: bool = False
    no_reply_template_sent: bool = False
    final_reminder_sent: bool = False


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class RuleAction(str, Enum):
    """What the system should do as a result of running the rule engine."""
    BLOCK_OPT_OUT = "block_opt_out"            # candidate opted out -> mark declined
    ESCALATE_TO_HUMAN = "escalate_to_human"     # sensitive topic -> human review
    NO_ACTION = "no_action"                     # interview scheduled or outreach complete
    SEND_TEMPLATE = "send_template"             # window constraint/milestone trigger -> send template
    PASS_TO_LLM = "pass_to_llm"                 # nothing triggered -> safe for text messaging layer


class TemplateType(str, Enum):
    """Which approved WhatsApp template should be sent, when SEND_TEMPLATE
    is the resolved action. The caller (webhook/orchestration layer) maps
    this to a concrete Twilio Content SID before calling
    ``messaging.send_template_message``.
    """
    JOB_OUTREACH = "job_outreach"
    NO_REPLY = "no_reply"
    FINAL_REMINDER = "final_reminder"
    INTERVIEW_FOLLOW_UP = "interview_follow_up"


@dataclass
class RuleResult:
    action: RuleAction
    reason: str                 # human-readable explanation, useful for logs/dashboard
    matched_keyword: Optional[str] = None
    template_type: Optional[TemplateType] = None

    @property
    def passed(self) -> bool:
        """True only if the message is clear to go to the LLM."""
        return self.action == RuleAction.PASS_TO_LLM


# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

OPT_OUT_KEYWORDS = [
    "stop",
    "unsubscribe",
    "opt out",
    "opt-out",
    "remove me",
    "do not contact",
    "don't contact",
]

ESCALATION_KEYWORDS = [
    "visa",
    "sponsorship",
    "work permit",
    "complaint",
    "lawyer",
    "legal action",
    "discriminat",   # catches "discrimination" / "discriminatory"
    "harass",        # catches "harass" / "harassment" / "harassed"
]

WHATSAPP_REPLY_WINDOW_HOURS = 24


# ---------------------------------------------------------------------------
# Internal decision helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and strip whitespace so keyword matching is consistent."""
    return text.strip().lower()


def hours_since(past_time: datetime, now: datetime) -> float:
    """Calculate the floating-point hours elapsed since a timestamp."""
    delta = now - past_time
    return delta.total_seconds() / 3600.0


def conversation_window_expired(last_msg_at: Optional[datetime], now: datetime) -> bool:
    """Check if the 24-hour customer service session window has expired."""
    if last_msg_at is None:
        return True
    return hours_since(last_msg_at, now) >= WHATSAPP_REPLY_WINDOW_HOURS


def needs_initial_outreach(state: CandidateState) -> bool:
    """Rule A: Candidate has never been contacted before."""
    return state.first_outreach_sent_at is None


def needs_no_reply_template(state: CandidateState, now: datetime) -> bool:
    """Rule B: Candidate never replied, 24 hours elapsed since outreach, and template not yet sent."""
    if state.first_outreach_sent_at is not None and state.last_candidate_message_at is None:
        if not state.no_reply_template_sent:
            return hours_since(state.first_outreach_sent_at, now) >= WHATSAPP_REPLY_WINDOW_HOURS
    return False


def needs_final_reminder(state: CandidateState, now: datetime) -> bool:
    """Rule C: Candidate still never replied, 24 hours elapsed since NO_REPLY template, and not yet sent."""
    if state.first_outreach_sent_at is not None and state.last_candidate_message_at is None:
        if state.no_reply_template_sent and not state.final_reminder_sent:
            if state.last_template_sent_at is not None:
                return hours_since(state.last_template_sent_at, now) >= WHATSAPP_REPLY_WINDOW_HOURS
    return False


def needs_interview_followup(state: CandidateState, now: datetime) -> bool:
    """Rule D: Candidate replied previously, but the 24-hour conversation window has elapsed."""
    if state.last_candidate_message_at is not None and not state.interview_scheduled:
        return conversation_window_expired(state.last_candidate_message_at, now)
    return False


# ---------------------------------------------------------------------------
# Individual deterministic checks
# ---------------------------------------------------------------------------

def check_opt_out(message: str) -> Optional[RuleResult]:
    """Returns a RuleResult if the message is an opt-out request, else None."""
    normalized = _normalize(message)
    for keyword in OPT_OUT_KEYWORDS:
        if keyword in normalized:
            return RuleResult(
                action=RuleAction.BLOCK_OPT_OUT,
                reason=f"Message matched opt-out keyword '{keyword}'",
                matched_keyword=keyword,
            )
    return None


def check_escalation(message: str) -> Optional[RuleResult]:
    """Returns a RuleResult if the message contains a sensitive escalation topic, else None."""
    normalized = _normalize(message)
    for keyword in ESCALATION_KEYWORDS:
        if keyword in normalized:
            return RuleResult(
                action=RuleAction.ESCALATE_TO_HUMAN,
                reason=f"Message matched escalation keyword '{keyword}'",
                matched_keyword=keyword,
            )
    return None


# ---------------------------------------------------------------------------
# Public Gate function
# ---------------------------------------------------------------------------

def gate_message(
    message: str,
    candidate_state: Optional[CandidateState] = None,
    now: Optional[datetime] = None,
) -> RuleResult:
    """
    Runs all recruitment business rules in hierarchical priority order.
    Returns the first matching non-Null operational action result.

    Priority Flow:
      1. Opt-out check (Compliance priority)
      2. Human Escalation check (Risk priority)
      3. Interview Status Verification (Completed workflows)
      4. Initial outreach evaluation
      5. No Reply milestone tracking
      6. Final Outreach milestone tracking
      7. Re-engagement session window evaluations
      8. Pass to conversational LLM layer
    """
    now = now or datetime.now(timezone.utc)
    state = candidate_state or CandidateState()

    # 1. Opt-out Compliance
    opt_out_res = check_opt_out(message)
    if opt_out_res:
        return opt_out_res

    # 2. Risk Escalation
    escalation_res = check_escalation(message)
    if escalation_res:
        return escalation_res

    # 3. Rule F: Interview Scheduled Check
    if state.interview_scheduled:
        return RuleResult(
            action=RuleAction.NO_ACTION,
            reason="Interview is already scheduled. No automated action required."
        )

    # 4. Rule A: Initial Outreach
    if needs_initial_outreach(state):
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason="First-ever outreach to this candidate.",
            template_type=TemplateType.JOB_OUTREACH,
        )

    # 5. Rule B: No Reply Reminder
    if needs_no_reply_template(state, now):
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason="Candidate has not replied within 24 hours of initial outreach.",
            template_type=TemplateType.NO_REPLY,
        )

    # 6. Rule C: Final Reminder
    if needs_final_reminder(state, now):
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason="Candidate has not replied within 24 hours of No Reply template.",
            template_type=TemplateType.FINAL_REMINDER,
        )

    # 7. Rule D: Interview Follow-Up
    if needs_interview_followup(state, now):
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason="Candidate has active history, but the 24-hour reply session window expired.",
            template_type=TemplateType.INTERVIEW_FOLLOW_UP,
        )

    # 8. Rule E: Fall through to chat session window (Pass to LLM layer)
    return RuleResult(
        action=RuleAction.PASS_TO_LLM,
        reason="Conversation window is active. Safe to pass to conversational LLM layer.",
    )
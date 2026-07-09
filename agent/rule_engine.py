"""
Rule engine for the WhatsApp Recruiting Agent.

This module sits BEFORE the LLM layer. Its job is to catch anything that
must be handled deterministically -- opt-outs, sensitive escalation topics,
and WhatsApp's 24-hour free-form messaging window -- so the LLM never has
to make a judgment call on these.

Public entry point: gate_message()
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class RuleAction(str, Enum):
    """What the system should do as a result of running the rule engine."""
    BLOCK_OPT_OUT = "block_opt_out"            # candidate opted out -> mark declined
    ESCALATE_TO_HUMAN = "escalate_to_human"     # sensitive topic -> human review
    SEND_TEMPLATE = "send_template"             # 24h window expired -> new template required
    PASS_TO_LLM = "pass_to_llm"                 # nothing triggered -> safe for the LLM layer


@dataclass
class RuleResult:
    action: RuleAction
    reason: str                 # human-readable explanation, useful for logs/dashboard
    matched_keyword: Optional[str] = None

    @property
    def passed(self) -> bool:
        """True only if the message is clear to go to the LLM."""
        return self.action == RuleAction.PASS_TO_LLM


# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------
# Kept as simple, editable lists rather than buried in logic, since these
# will likely need tuning after real-world testing.

OPT_OUT_KEYWORDS = [
    "stop",
    "unsubscribe",
    "opt out",
    "opt-out",
    "remove me",
    "do not contact",
    "don't contact",
]

# NOTE: salary/compensation-related terms ("salary", "compensation", "ctc",
# "pay range") were intentionally removed from this list -- these are now
# handled by the LLM layer instead of being force-escalated to a human.
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
# Individual rule checks
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and strip whitespace so keyword matching is consistent."""
    return text.strip().lower()


def check_opt_out(message: str) -> Optional[RuleResult]:
    """
    Returns a RuleResult if the message is an opt-out request, else None.
    Opt-outs are checked first because they are a legal/compliance requirement
    -- once a candidate opts out, nothing else should be auto-sent to them.
    """
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
    """
    Returns a RuleResult if the message contains a sensitive topic that
    should always go to a human (visa, complaints, etc.), else None.
    """
    normalized = _normalize(message)
    for keyword in ESCALATION_KEYWORDS:
        if keyword in normalized:
            return RuleResult(
                action=RuleAction.ESCALATE_TO_HUMAN,
                reason=f"Message matched escalation keyword '{keyword}'",
                matched_keyword=keyword,
            )
    return None


def check_reply_window(
    last_candidate_message_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[RuleResult]:
    """
    Returns a RuleResult if WhatsApp's 24-hour free-form reply window has
    expired, else None.

    last_candidate_message_at: timestamp of the candidate's most recent
        inbound message (this is what starts/refreshes the 24h window).
        If None, there is no open window at all (e.g. first-ever contact),
        so a template is required.
    now: defaults to the current time; injectable for testing.
    """
    now = now or datetime.now(timezone.utc)

    if last_candidate_message_at is None:
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason="No prior inbound message from candidate; no open reply window.",
        )

    window_expires_at = last_candidate_message_at + timedelta(
        hours=WHATSAPP_REPLY_WINDOW_HOURS
    )

    if now >= window_expires_at:
        return RuleResult(
            action=RuleAction.SEND_TEMPLATE,
            reason=(
                f"24-hour free-form reply window expired at "
                f"{window_expires_at.isoformat()} (now: {now.isoformat()})."
            ),
        )

    return None


# ---------------------------------------------------------------------------
# Gate function
# ---------------------------------------------------------------------------

def gate_message(
    message: str,
    last_candidate_message_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> RuleResult:
    """
    Runs every rule, in priority order, and returns the first one that
    triggers. Only returns PASS_TO_LLM if NONE of the rules trigger.

    Priority order matters:
      1. Opt-out      -- compliance, always wins
      2. Escalation    -- sensitive topics, always go to a human
      3. Reply window  -- platform constraint, must send template if expired
      4. Otherwise, pass to the LLM layer
    """
    for check in (
        lambda: check_opt_out(message),
        lambda: check_escalation(message),
        lambda: check_reply_window(last_candidate_message_at, now),
    ):
        result = check()
        if result is not None:
            return result

    return RuleResult(
        action=RuleAction.PASS_TO_LLM,
        reason="No rule triggered; safe to pass to LLM layer.",
    )
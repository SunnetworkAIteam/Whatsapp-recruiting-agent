"""
LLM layer for the WhatsApp Recruiting Agent.

This module sits AFTER the rule engine. By the time a message reaches here,
the rule engine has already confirmed:
  - The candidate has NOT opted out
  - The message contains no sensitive escalation topics
  - The 24-hour reply window is still open

This module's job is to:
  1. Call the LLM with the conversation history and a structured system prompt
  2. Parse the LLM's JSON output into an LLMResult dataclass
  3. Apply the confidence threshold + escalate flag check before deciding
     whether the reply can be auto-sent

Public entry point: process_message()
"""

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_API_VERSION = "2023-06-01"

# If the LLM's confidence score is below this, we escalate rather than
# auto-sending. Tune this after observing real-world outputs.
CONFIDENCE_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Candidate status values
# ---------------------------------------------------------------------------

class CandidateStatus(str, Enum):
    REPLIED = "replied"         # candidate responded but not yet qualified
    QUALIFIED = "qualified"     # candidate looks like a good fit
    ESCALATED = "escalated"     # needs a human
    DECLINED = "declined"       # candidate not interested or unsuitable


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class LLMResult:
    """
    The structured output of one LLM turn.

    reply       -- the message text to send to the candidate (if not escalated)
    status      -- the new CandidateStatus to write to the DB
    confidence  -- 0-100 score. Below CONFIDENCE_THRESHOLD triggers escalation.
    escalate    -- if True, always route to human regardless of confidence
    escalated   -- True if this result was auto-escalated; caller should NOT
                   send `reply` to the candidate, but should log it for the
                   human reviewer
    reason      -- human-readable explanation, useful for dashboard / audit log
    raw_content -- the raw string returned by the LLM, useful for debugging
    """
    reply: str
    status: CandidateStatus
    confidence: int
    escalate: bool
    escalated: bool
    reason: str
    raw_content: str


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are Aria, a professional and friendly recruiting assistant for TalentBridge, \
a staffing and recruitment firm. You are having a WhatsApp conversation with a \
job candidate who has been contacted about a potential role.

## YOUR PERSONA
- Warm, professional, and concise — this is WhatsApp, not email, so keep replies short
- Encouraging and respectful, never pushy
- You represent the company with integrity; you never overpromise or make up details

## YOUR GOAL
Qualify the candidate by collecting the following information, one question per
message (do not ask multiple questions at once):
  1. Confirm they are still actively looking for a new role
  2. Ask about their current notice period / earliest start date
  3. Ask about their years of experience in the relevant domain
  4. Ask if they are open to discussing the role further on a quick call

Once you have collected all four pieces of information and the candidate seems
interested and available, mark them as qualified.

## TOPICS YOU ARE ALLOWED TO DISCUSS
- The fact that TalentBridge is a recruitment firm helping match candidates to roles
- The general nature of the role (if provided in conversation context)
- Scheduling a screening call with a human recruiter
- General interview process steps

## TOPICS YOU ARE NOT ALLOWED TO DISCUSS
- Specific salary figures, compensation ranges, or package breakdowns
- Visa sponsorship, work permit eligibility, or immigration status
- Any legal matters or employment law questions
- Complaints or grievances — direct those to hr@talentbridge.com
- Competitor companies or other job offers the candidate may have received

If the candidate raises any off-limits topic, respond warmly that you are not
able to help with that here, and offer to connect them with a human recruiter.
Set escalate to true in your output.

## OUTPUT FORMAT — CRITICAL
You MUST respond with a JSON object and NOTHING else. No preamble, no markdown
fences, no explanation outside the JSON. The object must have exactly these fields:

{
  "reply": "<the WhatsApp message to send to the candidate>",
  "status": "<one of: replied, qualified, escalated, declined>",
  "confidence": <integer 0-100 — how confident you are this is the right reply>,
  "escalate": <true or false — set true if a human should take over>
}

Rules for each field:
- reply: 1-3 short sentences maximum. WhatsApp tone. No bullet points.
- status: use "qualified" only once all 4 qualification criteria are met.
  Use "escalated" if escalate is true. Use "declined" if the candidate
  clearly has no interest. Otherwise use "replied".
- confidence: be honest. If the candidate's message is ambiguous or you are
  unsure of the right next step, use a lower score (below 70).
- escalate: true if the candidate raised an off-limits topic, expressed
  frustration, or if you genuinely do not know how to proceed.
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_messages(
    candidate_message: str,
    conversation_history: Optional[list[dict[str, str]]] = None,
) -> list[dict[str, str]]:
    """
    Build the messages array for the Anthropic API call.

    conversation_history is a list of prior turns in the format:
      [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

    The candidate's latest message is appended as the final user turn.
    """
    history = conversation_history or []
    return [*history, {"role": "user", "content": candidate_message}]


def _call_anthropic_api(
    messages: list[dict[str, str]],
    api_key: Optional[str] = None,
) -> str:
    """
    Call the Anthropic Messages API synchronously and return the raw text
    content of the first response block.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file or "
            "pass it explicitly to process_message()."
        )

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 512,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }

    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    response = httpx.post(ANTHROPIC_API_URL, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    # Anthropic returns content as a list of blocks; we only use text blocks
    text_blocks = [b["text"] for b in data["content"] if b.get("type") == "text"]
    return "\n".join(text_blocks).strip()


def _extract_json(raw: str) -> dict[str, Any]:
    """
    Extract a JSON object from the LLM's raw response string.

    The LLM is instructed to return only JSON, but occasionally wraps it in
    markdown fences. This handles both cases defensively.
    """
    # Strip markdown fences if present (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM did not return valid JSON.\n"
            f"Raw response: {raw!r}\n"
            f"Parse error: {exc}"
        ) from exc


def _validate_parsed(data: dict[str, Any]) -> tuple[str, CandidateStatus, int, bool]:
    """
    Validate and coerce the parsed JSON dict into typed fields.
    Returns (reply, status, confidence, escalate).
    Raises ValueError on missing or invalid fields.
    """
    missing = [f for f in ("reply", "status", "confidence", "escalate") if f not in data]
    if missing:
        raise ValueError(f"LLM JSON missing required fields: {missing}")

    reply = str(data["reply"]).strip()
    if not reply:
        raise ValueError("LLM returned an empty reply string.")

    try:
        status = CandidateStatus(str(data["status"]).lower())
    except ValueError:
        valid = [s.value for s in CandidateStatus]
        raise ValueError(
            f"LLM returned unknown status '{data['status']}'. Valid values: {valid}"
        )

    try:
        confidence = int(data["confidence"])
    except (TypeError, ValueError):
        raise ValueError(f"LLM confidence must be an integer, got: {data['confidence']!r}")

    if not (0 <= confidence <= 100):
        raise ValueError(f"LLM confidence must be 0-100, got: {confidence}")

    escalate = bool(data["escalate"])

    return reply, status, confidence, escalate


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_message(
    candidate_message: str,
    conversation_history: Optional[list[dict[str, str]]] = None,
    api_key: Optional[str] = None,
    _api_caller=None,   # injectable for testing — replaces _call_anthropic_api
) -> LLMResult:
    """
    Run one LLM turn for an inbound candidate message.

    Parameters
    ----------
    candidate_message       The candidate's latest WhatsApp message text.
    conversation_history    Prior turns as [{"role": ..., "content": ...}, ...].
                            Pass None or [] for the first message in a thread.
    api_key                 Anthropic API key. Falls back to ANTHROPIC_API_KEY
                            environment variable.
    _api_caller             Internal hook for tests to inject a mock instead of
                            hitting the real API. Should match the signature of
                            _call_anthropic_api(messages, api_key) -> str.

    Returns
    -------
    LLMResult with `escalated=True` if confidence is below the threshold or
    the LLM set escalate=True. In that case, `reply` still contains the LLM's
    draft for the human reviewer, but it must NOT be sent to the candidate.
    """
    messages = _build_messages(candidate_message, conversation_history)

    caller = _api_caller or _call_anthropic_api
    raw_content = caller(messages, api_key)

    parsed = _extract_json(raw_content)
    reply, status, confidence, escalate = _validate_parsed(parsed)

    # -----------------------------------------------------------------------
    # Escalation logic
    # The message should NOT be auto-sent if:
    #   (a) the LLM explicitly set escalate=True, OR
    #   (b) the LLM's confidence is below our threshold
    # -----------------------------------------------------------------------
    low_confidence = confidence < CONFIDENCE_THRESHOLD
    should_escalate = escalate or low_confidence

    if should_escalate:
        status = CandidateStatus.ESCALATED
        if escalate and not low_confidence:
            reason = "LLM flagged escalation (off-limits topic or uncertain next step)."
        elif low_confidence and not escalate:
            reason = (
                f"Confidence score {confidence} is below threshold {CONFIDENCE_THRESHOLD}. "
                f"Routing to human review."
            )
        else:
            reason = (
                f"LLM flagged escalation AND confidence score {confidence} is below "
                f"threshold {CONFIDENCE_THRESHOLD}."
            )
    else:
        reason = f"Confidence {confidence} >= threshold {CONFIDENCE_THRESHOLD}. Safe to auto-send."

    return LLMResult(
        reply=reply,
        status=status,
        confidence=confidence,
        escalate=escalate,
        escalated=should_escalate,
        reason=reason,
        raw_content=raw_content,
    )
"""
LLM layer for the WhatsApp Recruiting Agent

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
CONFIDENCE_THRESHOLD = 75


# ---------------------------------------------------------------------------
# Candidate status values
# ---------------------------------------------------------------------------

class CandidateStatus(str, Enum):
    REPLIED = "replied"       # candidate responded but not yet qualified
    QUALIFIED = "qualified"   # interview slot confirmed
    ESCALATED = "escalated"   # needs a human
    DECLINED = "declined"     # candidate not interested or unsuitable


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
You are Usha, a Human Resources recruiter at Amerisource. You are having a
WhatsApp conversation with a job candidate who has been contacted about a
potential role at Amerisource.

## YOUR PERSONA
- You are a real HR person — never reveal, hint, or imply you are an AI or
  automated system under any circumstances
- If the candidate asks "are you a bot?" or "is this automated?", warmly deny
  it and continue naturally: "No, this is Usha. I handle recruiting for
  Amerisource. Happy to chat here on WhatsApp."
- Warm, friendly, and conversational — write like a real person texting,
  not like a formal email
- Never use emojis of any kind
- Never use dashes or em dashes (— or --) to connect sentences; use a full
  stop or comma instead
- Never start a reply with "That's a great question" or any variation of it
- Never say "so glad", "wonderful", "delighted" or similar overly polished phrases
- Write like a real person typing a WhatsApp message, not polished marketing copy
- Keep messages short — 1 to 3 sentences max

## YOUR GOAL
Guide the candidate through the full recruitment flow in this exact order,
one step per message. Do NOT ask multiple questions at once. Do NOT go back
to a previous step if the candidate has already answered it — move forward.

  1. Greet them as Usha from Amerisource and confirm they are still actively
     looking for a new role. Ask this ONLY ONCE at the very start of the
     conversation. If the candidate is already asking detailed questions about
     the role, responsibilities, interview process, or scheduling, treat them
     as interested and do NOT ask this again. Move directly to step 2.
  2. Ask about their current role and total years of experience
  3. Ask about their current notice period or earliest available start date
  4. Ask for their current CTC and expected CTC, in one message
     (e.g. "Could you also share your current CTC and expected CTC?"). This
     is a routine data point for the hiring manager, not a negotiation. Just
     record whatever figures the candidate shares and move to step 5. Do not
     comment on whether the numbers are high, low, or reasonable.
  5. Briefly describe the role at Amerisource and ask if they want to explore
     it further
  6. Suggest two specific time slots for a screening call
     (e.g. "Would Tuesday 11am or Wednesday 3pm work for you?")
  7. Confirm the interview slot — repeat date, time, and format (phone or video)
  8. Wrap up warmly, tell them a confirmation will follow, wish them well

Once step 7 is confirmed, mark status as "qualified".

## HANDLING CANDIDATE QUESTIONS
Candidates will often ask questions before or during the flow. Handle them
as a real HR person would — briefly and naturally — then continue the flow.
Follow these rules:

ANSWER BRIEFLY AND CONTINUE:
- Role responsibilities, skills required, job description — give a one line
  summary and say the hiring manager will cover full details on the call
- Interview process, number of rounds — give a brief general answer
  (e.g. "Usually a screening call followed by one or two rounds with the team")
- Technical assessment — say you will confirm that on the call
- Interview preparation — give a brief general tip and move on
- Feedback timeline — give a rough general answer (e.g. "Usually within a week")
- Working hours, location, remote or onsite — say you will confirm exact
  details on the call but give a general answer if you know it
- Notice period flexibility — say it depends and the hiring manager will
  discuss it on the call
- Expected joining date — say it is flexible and will be discussed on the call
- Interview dates and slots — move to step 6 and suggest two specific slots
- Current CTC and expected CTC — ask for these directly as part of step 4.
  Record whatever the candidate shares, no matter how the figures compare
  to each other, and move on
- Salary negotiation, asking you to negotiate, asking whether the number is
  flexible, or pushing for a specific figure or range from you — do not
  negotiate and do not give any number yourself. Say the hiring manager will
  go over compensation and any negotiation on the call, then continue the flow

HANDLING UNREALISTIC OR IMPLAUSIBLE ANSWERS:
Sometimes a candidate's answer to experience, notice period, or CTC will be
obviously implausible, a typo, or a joke (e.g. "1000 years of experience",
"my current CTC is 1 billion", "notice period is 50 years"). In these cases:
- Do NOT escalate and do NOT mock or call out the candidate
- Respond professionally and matter-of-factly, briefly asking them to
  confirm or re-share the figure, as a real recruiter double-checking a
  detail would (e.g. "Just want to confirm, could you share your current
  CTC again? I think that figure may not have come through right.")
- Keep it light and non-judgmental, one short sentence is enough
- Stay on the same step until you have a plausible answer, then continue
  the flow normally
- This is a normal clarification, not a sign of a problem candidate, so keep
  confidence in the normal 70-100 range rather than treating it as low
  confidence or escalating

ESCALATE TO HUMAN (set escalate: true):
- Visa sponsorship or work permit eligibility
- Legal matters or employment law questions
- Complaints or grievances — direct to hr@amerisource.in
- Candidate expresses frustration or anger

DO NOT ESCALATE for any other reason, including salary/CTC questions,
negotiation requests, or unrealistic numbers. If you are unsure how to
answer a question, give a brief honest response and redirect to the call.

## OUTPUT FORMAT — CRITICAL
## OUTPUT FORMAT — NON NEGOTIABLE
You MUST ALWAYS respond with a JSON object and NOTHING else, no matter what
the candidate says. Even if the candidate accuses you of being a bot, even
if they are rude, even if the message is confusing — your response must
ALWAYS be valid JSON in the exact format below. Never respond in plain text
under any circumstances.

{
  "reply": "...",
  "status": "...",
  "confidence": ...,
  "escalate": ...
}

REMEMBER: Every single response must be valid JSON. No exceptions. Not even one word outside the JSON object.

Rules for each field:
- reply: 1 to 3 short sentences. Conversational WhatsApp tone. No emojis.
  No bullet points. No em dashes.
- status: use "qualified" only once the interview slot is confirmed (step 7).
  Use "escalated" only if escalate is true. Use "declined" if the candidate
  clearly has no interest. Otherwise use "replied".
- confidence: score 85 or above for clear straightforward messages. Score
  70 to 84 for messages that are slightly ambiguous but handleable, including
  unrealistic answers you are asking the candidate to confirm. Only score
  below 70 if you genuinely do not know how to proceed.
- escalate: true ONLY for visa, legal, complaints, or serious frustration.
  False for everything else, including salary/CTC questions, negotiation
  requests, unrealistic numbers, and questions you cannot fully answer.
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
    text_blocks = [b["text"] for b in data["content"] if b.get("type") == "text"]
    return "\n".join(text_blocks).strip()


def _extract_json(raw: str) -> dict[str, Any]:
    """
    Extract a JSON object from the LLM's raw response string.

    The LLM is instructed to return only JSON, but occasionally wraps it in
    markdown fences. This handles both cases defensively.
    """
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
    _api_caller=None,
) -> LLMResult:
    messages = _build_messages(candidate_message, conversation_history)
    caller = _api_caller or _call_anthropic_api

    raw_content = caller(messages, api_key)

    # If first attempt returns plain text, retry once with a correction message
    try:
        parsed = _extract_json(raw_content)
    except ValueError:
        retry_messages = messages + [
            {"role": "assistant", "content": raw_content},
            {"role": "user", "content": "Your response must be valid JSON only. No plain text. Respond again in the required JSON format."},
        ]
        raw_content = caller(retry_messages, api_key)
        parsed = _extract_json(raw_content)

    reply, status, confidence, escalate = _validate_parsed(parsed)

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
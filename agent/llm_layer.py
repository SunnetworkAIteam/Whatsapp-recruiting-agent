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
from .prompts import SYSTEM_PROMPT
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
    candidate_name: Optional[str] = None
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
    system_prompt: Optional[str] = None,
) -> str:
    """
    Call the Anthropic Messages API synchronously and return the raw text
    content of the first response block.

    system_prompt: overrides the module-level SYSTEM_PROMPT for this call
        (used to append the "ask for the candidate's name" instruction
        when we don't have one on file -- see process_message).

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
        "system": system_prompt or SYSTEM_PROMPT,
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
    current_status: Optional[CandidateStatus] = None,
    candidate_name: Optional[str] = None,
    job_description: Optional[str] = None,
    company_name: Optional[str] = None,
    company_website: Optional[str] = None,
    calendly_link: Optional[str] = None,
    _api_caller=None,
) -> LLMResult:
    """
    current_status: the candidate's status as currently stored in the DB,
        BEFORE this message is processed. If it is CandidateStatus.ESCALATED,
        a human has already taken over this conversation, so the LLM must
        NOT be called and NOT generate a reply. This is enforced here in
        code rather than left to the LLM's own judgment, since a prompt
        instruction alone is not a reliable guarantee once a conversation
        has already been handed to a human reviewer.
    """
    if current_status == CandidateStatus.ESCALATED:
        return LLMResult(
            reply="",
            status=CandidateStatus.ESCALATED,
            confidence=0,
            escalate=True,
            escalated=True,
            reason=(
                "Conversation is already escalated to a human. LLM was not "
                "called; no auto-reply should be sent. Candidate's new "
                "message has been logged for the human reviewer."
            ),
            raw_content="",
        )

    messages = _build_messages(candidate_message, conversation_history)
    caller = _api_caller or _call_anthropic_api

    # If we don't have a real name for this candidate yet (e.g. they
    # messaged in from an unrecognized number -- see
    # app/status.py's create_candidate_for_unknown_number), instruct the
    # LLM to ask for it before continuing the normal recruitment flow,
    # and to report back whatever name they give in a new optional
    # "candidate_name" JSON field, so the caller (agent/pipeline.py) can
    # save it to the candidate's record. Without this, the bot would
    # never learn or use their real name at all.
    has_real_name = bool(candidate_name) and candidate_name.strip().lower() not in ("", "unknown")
    if has_real_name:
        effective_system_prompt = SYSTEM_PROMPT
    else:
        effective_system_prompt = SYSTEM_PROMPT + """

## MISSING CANDIDATE NAME
We do not have this candidate's name on file yet. Before continuing the
normal recruitment flow (Step 1 onward), politely ask for their name
first -- unless they already gave it earlier in this conversation. Once
they tell you their name, include it in your JSON response as
"candidate_name" (their name, exactly as they gave it). If you still
don't know their name, omit "candidate_name" from the response (or set
it to null).
"""

    # Role-specific context (company, job description) and the global
    # interview booking link, if configured -- see the Settings tab
    # (role_work_details.job_description/company_name/company_website,
    # and app_settings.calendly_link). This was previously silently
    # missing entirely: the parameters existed but were never actually
    # appended to the system prompt, so the LLM never saw this context
    # at all despite the data being correctly fetched upstream.
    context_lines = []
    if company_name:
        context_lines.append(
            f"Client company for this role: {company_name}" + (f" ({company_website})" if company_website else "")
        )
    if job_description:
        context_lines.append(f"Job description for this role:\n{job_description}")
    if calendly_link:
        context_lines.append(f"Interview booking link to share at Step 6: {calendly_link}")

    if context_lines:
        effective_system_prompt += "\n\n## ROLE-SPECIFIC CONTEXT\n" + "\n\n".join(context_lines)


    raw_content = caller(messages, api_key, system_prompt=effective_system_prompt)

    # If first attempt returns plain text, retry once with a correction message
    try:
        parsed = _extract_json(raw_content)
    except ValueError:
        retry_messages = messages + [
            {"role": "assistant", "content": raw_content},
            {"role": "user", "content": "Your response must be valid JSON only. No plain text. Respond again in the required JSON format."},
        ]
        raw_content = caller(retry_messages, api_key, system_prompt=effective_system_prompt)
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

    captured_name = parsed.get("candidate_name")
    if captured_name is not None and not str(captured_name).strip():
        captured_name = None

    return LLMResult(
        reply=reply,
        status=status,
        confidence=confidence,
        escalate=escalate,
        escalated=should_escalate,
        reason=reason,
        raw_content=raw_content,
        candidate_name=captured_name,
    )
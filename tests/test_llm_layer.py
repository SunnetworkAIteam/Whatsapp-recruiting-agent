"""
Tests for agent/llm_layer.py

The Anthropic API is NEVER called here. Every test uses a mock caller
injected via the _api_caller parameter so the suite runs fully offline,
is deterministic, and doesn't cost API credits.

Run with:
    pytest tests/test_llm_layer.py -v
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

# Add the project root to Python's import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json

import pytest

from agent.llm_layer import (
    CONFIDENCE_THRESHOLD,
    CandidateStatus,
    LLMResult,
    _build_messages,
    _extract_json,
    _validate_parsed,
    process_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_api_response(
    reply: str = "Thanks for getting back to us! Are you currently open to new opportunities?",
    status: str = "replied",
    confidence: int = 85,
    escalate: bool = False,
) -> str:
    """Return a JSON string that looks like what the LLM would output."""
    return json.dumps({
        "reply": reply,
        "status": status,
        "confidence": confidence,
        "escalate": escalate,
    })


def mock_caller(response_str: str):
    """
    Return a function that acts as a drop-in replacement for _call_anthropic_api
    but always returns response_str, ignoring its arguments.
    """
    def _caller(messages, api_key=None):
        return response_str
    return _caller


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_no_history_produces_single_user_turn(self):
        messages = _build_messages("Hi, I'm interested!")
        assert messages == [{"role": "user", "content": "Hi, I'm interested!"}]

    def test_history_is_prepended_before_new_message(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        messages = _build_messages("What's next?", conversation_history=history)
        assert len(messages) == 3
        assert messages[-1] == {"role": "user", "content": "What's next?"}

    def test_empty_history_list_treated_same_as_none(self):
        assert _build_messages("hey", []) == _build_messages("hey", None)


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_parses_plain_json(self):
        raw = '{"reply": "Hi!", "status": "replied", "confidence": 80, "escalate": false}'
        result = _extract_json(raw)
        assert result["reply"] == "Hi!"

    def test_strips_json_markdown_fence(self):
        raw = '```json\n{"reply": "Hi!", "status": "replied", "confidence": 80, "escalate": false}\n```'
        result = _extract_json(raw)
        assert result["status"] == "replied"

    def test_strips_plain_markdown_fence(self):
        raw = '```\n{"reply": "Hi!", "status": "replied", "confidence": 80, "escalate": false}\n```'
        result = _extract_json(raw)
        assert result["confidence"] == 80

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="LLM did not return valid JSON"):
            _extract_json("This is just plain text, not JSON.")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            _extract_json("")


# ---------------------------------------------------------------------------
# _validate_parsed
# ---------------------------------------------------------------------------

class TestValidateParsed:
    def _valid(self, **overrides):
        base = {"reply": "Let's chat!", "status": "replied", "confidence": 80, "escalate": False}
        return {**base, **overrides}

    def test_valid_data_returns_correct_tuple(self):
        reply, status, confidence, escalate = _validate_parsed(self._valid())
        assert reply == "Let's chat!"
        assert status == CandidateStatus.REPLIED
        assert confidence == 80
        assert escalate is False

    def test_all_valid_statuses_parse(self):
        for s in ("replied", "qualified", "escalated", "declined"):
            _, status, _, _ = _validate_parsed(self._valid(status=s))
            assert status == CandidateStatus(s)

    def test_missing_field_raises(self):
        data = {"reply": "Hi", "status": "replied", "confidence": 80}  # missing escalate
        with pytest.raises(ValueError, match="missing required fields"):
            _validate_parsed(data)

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError, match="unknown status"):
            _validate_parsed(self._valid(status="pending"))

    def test_non_integer_confidence_raises(self):
        with pytest.raises(ValueError, match="confidence must be an integer"):
            _validate_parsed(self._valid(confidence="high"))

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="0-100"):
            _validate_parsed(self._valid(confidence=150))

    def test_empty_reply_raises(self):
        with pytest.raises(ValueError, match="empty reply"):
            _validate_parsed(self._valid(reply="   "))


# ---------------------------------------------------------------------------
# process_message — happy path
# ---------------------------------------------------------------------------

class TestProcessMessageHappyPath:
    def test_returns_llm_result_on_valid_response(self):
        result = process_message(
            "Yes, I am looking!",
            _api_caller=mock_caller(make_api_response()),
        )
        assert isinstance(result, LLMResult)

    def test_high_confidence_not_escalated(self):
        result = process_message(
            "Yes, I'm interested.",
            _api_caller=mock_caller(make_api_response(confidence=90)),
        )
        assert result.escalated is False
        assert result.status == CandidateStatus.REPLIED

    def test_reply_text_is_passed_through(self):
        result = process_message(
            "Sure, tell me more.",
            _api_caller=mock_caller(
                make_api_response(reply="Great! What is your notice period?")
            ),
        )
        assert result.reply == "Great! What is your notice period?"

    def test_qualified_status_passed_through_when_confident(self):
        result = process_message(
            "I can start in 2 weeks and have 5 years experience.",
            _api_caller=mock_caller(
                make_api_response(status="qualified", confidence=88)
            ),
        )
        assert result.status == CandidateStatus.QUALIFIED
        assert result.escalated is False

    def test_raw_content_is_stored(self):
        raw = make_api_response()
        result = process_message("Hi!", _api_caller=mock_caller(raw))
        assert result.raw_content == raw

    def test_conversation_history_is_forwarded(self):
        """Verify history is included in the messages passed to the caller."""
        captured = {}

        def capturing_caller(messages, api_key=None):
            captured["messages"] = messages
            return make_api_response()

        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        process_message("Next question?", conversation_history=history, _api_caller=capturing_caller)

        # history + new user turn = 3 messages
        assert len(captured["messages"]) == 3
        assert captured["messages"][-1]["content"] == "Next question?"


# ---------------------------------------------------------------------------
# process_message — escalation logic
# ---------------------------------------------------------------------------

class TestProcessMessageEscalation:
    def test_low_confidence_triggers_escalation(self):
        low = CONFIDENCE_THRESHOLD - 1
        result = process_message(
            "Not sure what I'm looking for right now...",
            _api_caller=mock_caller(make_api_response(confidence=low)),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_confidence_exactly_at_threshold_is_not_escalated(self):
        result = process_message(
            "Yes, I am interested.",
            _api_caller=mock_caller(make_api_response(confidence=CONFIDENCE_THRESHOLD)),
        )
        assert result.escalated is False

    def test_escalate_true_triggers_escalation_regardless_of_confidence(self):
        result = process_message(
            "What's the salary and visa situation?",
            _api_caller=mock_caller(
                make_api_response(escalate=True, confidence=90)
            ),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_both_low_confidence_and_escalate_true(self):
        low = CONFIDENCE_THRESHOLD - 10
        result = process_message(
            "Confused candidate message",
            _api_caller=mock_caller(
                make_api_response(escalate=True, confidence=low)
            ),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_escalated_reply_is_preserved_for_human_reviewer(self):
        """
        Even when escalated, reply should NOT be lost — a human needs
        to see what the LLM was going to say.
        """
        result = process_message(
            "I want to discuss the salary details.",
            _api_caller=mock_caller(
                make_api_response(
                    reply="I'm afraid I can't discuss salary here.",
                    escalate=True,
                )
            ),
        )
        assert result.escalated is True
        assert "salary" in result.reply.lower() or len(result.reply) > 0

    def test_escalated_result_has_reason(self):
        low = CONFIDENCE_THRESHOLD - 5
        result = process_message(
            "Hmm I don't know",
            _api_caller=mock_caller(make_api_response(confidence=low)),
        )
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ---------------------------------------------------------------------------
# process_message — error handling
# ---------------------------------------------------------------------------

class TestProcessMessageErrorHandling:
    def test_raises_on_invalid_json_from_api(self):
        with pytest.raises(ValueError, match="LLM did not return valid JSON"):
            process_message(
                "Hi!",
                _api_caller=mock_caller("Sorry, I can't help with that."),
            )

    def test_raises_on_missing_json_fields(self):
        incomplete = json.dumps({"reply": "Hi", "status": "replied"})
        with pytest.raises(ValueError, match="missing required fields"):
            process_message("Hi!", _api_caller=mock_caller(incomplete))

    def test_raises_when_no_api_key_and_no_mock(self):
        """Without a mock or an env var, the real caller should raise."""
        import os
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                process_message("Hi!")
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_handles_markdown_fenced_json_from_llm(self):
        """LLM occasionally wraps output in ```json fences; we handle it."""
        fenced = f"```json\n{make_api_response()}\n```"
        result = process_message("Hi!", _api_caller=mock_caller(fenced))
        assert result.status == CandidateStatus.REPLIED
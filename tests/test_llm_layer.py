"""
Tests for agent/llm_layer

The Anthropic API is NEVER called here. Every test uses a mock caller
injected via the _api_caller parameter so the suite runs fully offline,
is deterministic, and doesn't cost API credits.

Run with:
    pytest tests/test_llm_layer.py -v
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add the project root to Python's import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json

import pytest

from agent.llm_layer import (
    CONFIDENCE_THRESHOLD,
    SYSTEM_PROMPT,
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
    reply: str = "Hi! This is Usha from Amerisource. Are you currently open to new opportunities?",
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
# System prompt content checks
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_persona_is_usha_from_amerisource(self):
        assert "Usha" in SYSTEM_PROMPT
        assert "Amerisource" in SYSTEM_PROMPT

    def test_no_ai_disclosure_instruction_present(self):
        assert "never reveal" in SYSTEM_PROMPT.lower()
        assert "AI" in SYSTEM_PROMPT or "bot" in SYSTEM_PROMPT.lower()

    def test_bot_denial_script_is_present(self):
        # Prompt must tell the LLM how to respond if asked "are you a bot?"
        assert "are you a bot" in SYSTEM_PROMPT.lower()

    def test_seven_step_flow_is_present(self):
        # All 7 steps should be referenced
        for step in ["1.", "2.", "3.", "4.", "5.", "6.", "7."]:
            assert step in SYSTEM_PROMPT

    def test_qualified_only_after_slot_confirmed(self):
        assert "slot is confirmed" in SYSTEM_PROMPT.lower() or \
               "step 6" in SYSTEM_PROMPT.lower()

    def test_off_limits_topics_listed(self):
        for topic in ["salary", "visa", "legal", "complaint"]:
            assert topic in SYSTEM_PROMPT.lower()

    def test_amerisource_hr_email_in_prompt(self):
        assert "hr@amerisource.in" in SYSTEM_PROMPT

    def test_json_output_format_specified(self):
        for field in ["reply", "status", "confidence", "escalate"]:
            assert field in SYSTEM_PROMPT

    def test_two_slot_suggestion_instruction_present(self):
        # Usha should suggest two time options, not ask open-ended
        assert "two" in SYSTEM_PROMPT.lower() or "tuesday" in SYSTEM_PROMPT.lower()


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
            {"role": "assistant", "content": "Hi! This is Usha from Amerisource."},
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
            _extract_json("Sure thing, let me help you with that!")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            _extract_json("")


# ---------------------------------------------------------------------------
# _validate_parsed
# ---------------------------------------------------------------------------

class TestValidateParsed:
    def _valid(self, **overrides):
        base = {
            "reply": "Great! What role are you currently in?",
            "status": "replied",
            "confidence": 85,
            "escalate": False,
        }
        return {**base, **overrides}

    def test_valid_data_returns_correct_tuple(self):
        reply, status, confidence, escalate = _validate_parsed(self._valid())
        assert reply == "Great! What role are you currently in?"
        assert status == CandidateStatus.REPLIED
        assert confidence == 85
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
# process_message — recruitment flow (happy path)
# ---------------------------------------------------------------------------

class TestRecruitmentFlow:
    """
    Simulates the 7-step Usha recruitment flow using mocked LLM responses.
    Each test represents one stage of the conversation.
    """

    def test_step1_initial_greeting_replied(self):
        """Usha greets and asks if candidate is looking."""
        result = process_message(
            "Hi, who is this?",
            _api_caller=mock_caller(make_api_response(
                reply="Hi! This is Usha from Amerisource. Are you currently open to new opportunities?",
                status="replied",
                confidence=88,
            )),
        )
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step2_current_role_and_experience(self):
        """Usha asks about current role and years of experience."""
        result = process_message(
            "Yes I'm looking!",
            _api_caller=mock_caller(make_api_response(
                reply="Great! Could you tell me about your current role and how many years of experience you have?",
                status="replied",
                confidence=90,
            )),
        )
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step3_notice_period(self):
        """Usha asks about notice period after getting experience info."""
        result = process_message(
            "I'm a software engineer with 5 years experience.",
            _api_caller=mock_caller(make_api_response(
                reply="That's great experience! What's your current notice period or earliest start date?",
                status="replied",
                confidence=87,
            )),
        )
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step4_role_description(self):
        """Usha describes the role and checks interest."""
        result = process_message(
            "I can join in 30 days.",
            _api_caller=mock_caller(make_api_response(
                reply="Perfect! We have a Senior Engineer role at Amerisource focused on healthcare data platforms. Does that sound interesting to you?",
                status="replied",
                confidence=86,
            )),
        )
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step5_availability_with_two_slot_options(self):
        """Usha suggests two specific time slots."""
        result = process_message(
            "Yes, that sounds interesting!",
            _api_caller=mock_caller(make_api_response(
                reply="Would Tuesday at 11am or Wednesday at 3pm work for a quick screening call?",
                status="replied",
                confidence=91,
            )),
        )
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step6_slot_confirmed_marks_qualified(self):
        """Once slot is confirmed, status becomes qualified."""
        result = process_message(
            "Wednesday 3pm works for me!",
            _api_caller=mock_caller(make_api_response(
                reply="Perfect! I've got you down for Wednesday at 3pm. You'll receive a confirmation shortly.",
                status="qualified",
                confidence=95,
            )),
        )
        assert result.status == CandidateStatus.QUALIFIED
        assert result.escalated is False

    def test_step7_wrap_up_still_qualified(self):
        """Final wrap-up message keeps status as qualified."""
        result = process_message(
            "Great, thank you!",
            _api_caller=mock_caller(make_api_response(
                reply="Looking forward to speaking with you! Have a great day. 😊",
                status="qualified",
                confidence=93,
            )),
        )
        assert result.status == CandidateStatus.QUALIFIED
        assert result.escalated is False

    def test_candidate_declines(self):
        """Candidate shows no interest — should be marked declined."""
        result = process_message(
            "I'm not interested, please don't contact me again.",
            _api_caller=mock_caller(make_api_response(
                reply="Completely understood! I'll make a note of that. Wishing you all the best.",
                status="declined",
                confidence=92,
            )),
        )
        assert result.status == CandidateStatus.DECLINED
        assert result.escalated is False

    def test_conversation_history_carried_through(self):
        """Full conversation history is passed to the LLM caller."""
        captured = {}

        def capturing_caller(messages, api_key=None):
            captured["messages"] = messages
            return make_api_response()

        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": '{"reply": "Hi! This is Usha.", "status": "replied", "confidence": 88, "escalate": false}'},
            {"role": "user", "content": "Yes I am looking"},
            {"role": "assistant", "content": '{"reply": "Great! What is your current role?", "status": "replied", "confidence": 87, "escalate": false}'},
        ]
        process_message(
            "I am a data engineer with 4 years experience.",
            conversation_history=history,
            _api_caller=capturing_caller,
        )

        assert len(captured["messages"]) == 5  # 4 history + 1 new
        assert captured["messages"][-1]["content"] == "I am a data engineer with 4 years experience."


# ---------------------------------------------------------------------------
# process_message — escalation scenarios
# ---------------------------------------------------------------------------

class TestEscalationScenarios:
    def test_bot_question_triggers_escalation(self):
        """Candidate asks if they're talking to a bot."""
        result = process_message(
            "Wait, am I talking to a bot right now?",
            _api_caller=mock_caller(make_api_response(
                reply="No, this is Usha! I handle recruiting for Amerisource. Happy to chat here on WhatsApp.",
                escalate=True,
                confidence=72,
            )),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_salary_question_triggers_escalation(self):
        """Candidate asks about salary — off-limits topic."""
        result = process_message(
            "What's the salary range for this role?",
            _api_caller=mock_caller(make_api_response(
                reply="That's a great question — I'd rather have our hiring manager speak to you directly about that. Let's get a call scheduled first!",
                escalate=True,
                confidence=88,
            )),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_visa_question_triggers_escalation(self):
        """Candidate asks about visa sponsorship — off-limits topic."""
        result = process_message(
            "Do you offer visa sponsorship?",
            _api_caller=mock_caller(make_api_response(
                reply="That's a great question — I'd rather have our hiring manager speak to you directly about that. Let's get a call scheduled first!",
                escalate=True,
                confidence=85,
            )),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_low_confidence_triggers_escalation(self):
        """Ambiguous message leads to low confidence — routes to human."""
        low = CONFIDENCE_THRESHOLD - 1
        result = process_message(
            "Maybe... I don't know, it depends on a lot of things.",
            _api_caller=mock_caller(make_api_response(
                confidence=low,
                escalate=False,
            )),
        )
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_confidence_at_threshold_is_not_escalated(self):
        result = process_message(
            "Yes, I'm interested.",
            _api_caller=mock_caller(make_api_response(confidence=CONFIDENCE_THRESHOLD)),
        )
        assert result.escalated is False

    def test_escalated_draft_reply_preserved_for_human(self):
        """Even when escalated, reply is kept so the human reviewer can see it."""
        result = process_message(
            "I want to know the exact salary before I proceed.",
            _api_caller=mock_caller(make_api_response(
                reply="That's a great question — I'd rather have our hiring manager speak to you directly about that.",
                escalate=True,
            )),
        )
        assert result.escalated is True
        assert len(result.reply) > 0

    def test_escalated_result_includes_reason(self):
        low = CONFIDENCE_THRESHOLD - 5
        result = process_message(
            "Hmm not sure",
            _api_caller=mock_caller(make_api_response(confidence=low)),
        )
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ---------------------------------------------------------------------------
# process_message — error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_raises_on_plain_text_response(self):
        with pytest.raises(ValueError, match="LLM did not return valid JSON"):
            process_message(
                "Hi!",
                _api_caller=mock_caller("Sure, happy to help!"),
            )

    def test_raises_on_missing_fields(self):
        incomplete = json.dumps({"reply": "Hi Usha here!", "status": "replied"})
        with pytest.raises(ValueError, match="missing required fields"):
            process_message("Hi!", _api_caller=mock_caller(incomplete))

    def test_raises_when_no_api_key_and_no_mock(self):
        import os
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                process_message("Hi!")
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_handles_markdown_fenced_json(self):
        fenced = f"```json\n{make_api_response()}\n```"
        result = process_message("Hi!", _api_caller=mock_caller(fenced))
        assert result.status == CandidateStatus.REPLIED

    def test_raw_content_always_stored(self):
        raw = make_api_response()
        result = process_message("Hi!", _api_caller=mock_caller(raw))
        assert result.raw_content == raw
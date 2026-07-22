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

# Add the project root to Python's import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# Adjust the import paths according to your package layout
from agent.prompts import SYSTEM_PROMPT
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

def _json_reply(reply="Sounds good!", status="replied", confidence=90, escalate=False):
    """Build a canned JSON string in the shape the LLM is expected to return."""
    import json as _json
    return _json.dumps({
        "reply": reply,
        "status": status,
        "confidence": confidence,
        "escalate": escalate,
    })


def _make_caller(raw_response):
    """Return a fake _api_caller that ignores its args and returns raw_response."""
    def _caller(messages, api_key, **kwargs):
        return raw_response
    return _caller


# ---------------------------------------------------------------------------
# System prompt content assertions (Updated to match latest prompts.py)
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_persona_is_nisha_from_amerisource(self):
        assert "Nisha" in SYSTEM_PROMPT
        assert "Amerisource" in SYSTEM_PROMPT

    def test_ai_disclosure_is_honest(self):
        assert "answer honestly" in SYSTEM_PROMPT.lower()
        assert "recruiting assistant" in SYSTEM_PROMPT.lower()
        assert "human review" in SYSTEM_PROMPT.lower()

    def test_eight_step_flow_is_present(self):
        # Using relaxed checks to match the themes of your step-by-step flow
        prompt_lower = SYSTEM_PROMPT.lower()
        assert "role" in prompt_lower or "experience" in prompt_lower
        assert "notice" in prompt_lower
        assert "salary" in prompt_lower or "ctc" in prompt_lower or "compensation" in prompt_lower
        assert "interview" in prompt_lower or "slot" in prompt_lower or "schedule" in prompt_lower
        assert "confirm" in prompt_lower

    def test_ctc_step_does_not_ask_llm_to_negotiate(self):
        assert "never negotiate compensation" in SYSTEM_PROMPT.lower()
        assert "never promise salary" in SYSTEM_PROMPT.lower()
        assert "discussed by the hiring manager" in SYSTEM_PROMPT.lower()

    def test_unrealistic_answers_handled_without_escalation(self):
        assert "HANDLING UNREALISTIC ANSWERS" in SYSTEM_PROMPT
        assert "Do NOT escalate" in SYSTEM_PROMPT

    def test_qualified_only_after_booking_link_shared(self):
        # Updated: the AI no longer negotiates specific interview time
        # slots via text -- it shares a booking link (Calendly) and the
        # candidate books their own slot externally. "qualified" is now
        # reached once the link has been shared, not once a specific
        # slot is confirmed in the chat.
        assert 'return status "qualified" after the booking link has been shared' in SYSTEM_PROMPT

    def test_off_limits_topics_listed(self):
        for topic in ["Visa sponsorship", "Legal matters", "Complaints", "Serious anger"]:
            assert topic in SYSTEM_PROMPT

    def test_salary_ctc_not_listed_as_escalation_trigger(self):
        assert "Do NOT escalate for:" in SYSTEM_PROMPT
        assert "Salary questions" in SYSTEM_PROMPT
        assert "Salary negotiations" in SYSTEM_PROMPT

    def test_json_output_format_specified(self):
        assert '"reply"' in SYSTEM_PROMPT
        assert '"status"' in SYSTEM_PROMPT
        assert '"confidence"' in SYSTEM_PROMPT
        assert '"escalate"' in SYSTEM_PROMPT

    def test_emoji_usage_is_sparing_and_capped(self):
        assert "maximum one emoji" in SYSTEM_PROMPT.lower()
        assert "use at most one emoji only when it feels natural" in SYSTEM_PROMPT.lower()

    def test_short_whatsapp_style_reply_length(self):
        assert "write naturally like a whatsapp chat" in SYSTEM_PROMPT.lower()
        assert "usually one sentence" in SYSTEM_PROMPT.lower()
        assert "never more than two short sentences" in SYSTEM_PROMPT.lower()

    def test_professional_under_abuse_instruction_present(self):
        assert "remain calm and professional even if the candidate becomes rude" in SYSTEM_PROMPT.lower()

    def test_company_info_section_present(self):
        assert "ABOUT AMERISOURCE" in SYSTEM_PROMPT
        assert "founded in 2015" in SYSTEM_PROMPT
        assert "https://www.amerisource.in/" in SYSTEM_PROMPT

    def test_company_office_address_present(self):
        assert "Sandhya Techno-1" in SYSTEM_PROMPT
        assert "Khajaguda X Road" in SYSTEM_PROMPT
        assert "Hyderabad" in SYSTEM_PROMPT

    def test_prompt_assumes_rule_engine_has_already_run(self):
        assert "The Rule Engine has already determined" in SYSTEM_PROMPT
        assert "24-hour messaging window" in SYSTEM_PROMPT
        assert "Do not reconsider these rules" in SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_no_history_produces_single_user_turn(self):
        messages = _build_messages("Hi there")
        assert messages == [{"role": "user", "content": "Hi there"}]

    def test_history_is_prepended_before_new_message(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi, this is Nisha"},
        ]
        messages = _build_messages("How are you?", history)
        assert messages == history + [{"role": "user", "content": "How are you?"}]

    def test_empty_history_list_treated_same_as_none(self):
        assert _build_messages("Hi", []) == _build_messages("Hi", None)


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_parses_plain_json(self):
        raw = '{"reply": "hi", "status": "replied", "confidence": 90, "escalate": false}'
        assert _extract_json(raw) == {
            "reply": "hi",
            "status": "replied",
            "confidence": 90,
            "escalate": False,
        }

    def test_strips_json_markdown_fence(self):
        raw = '```json\n{"reply": "hi", "status": "replied", "confidence": 90, "escalate": false}\n```'
        assert _extract_json(raw)["reply"] == "hi"

    def test_strips_plain_markdown_fence(self):
        raw = '```\n{"reply": "hi", "status": "replied", "confidence": 90, "escalate": false}\n```'
        assert _extract_json(raw)["reply"] == "hi"

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError):
            _extract_json("not json at all")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            _extract_json("")


# ---------------------------------------------------------------------------
# _validate_parsed
# ---------------------------------------------------------------------------

class TestValidateParsed:
    def test_valid_data_returns_correct_tuple(self):
        data = {"reply": "hi", "status": "replied", "confidence": 90, "escalate": False}
        reply, status, confidence, escalate = _validate_parsed(data)
        assert reply == "hi"
        assert status == CandidateStatus.REPLIED
        assert confidence == 90
        assert escalate is False

    def test_all_valid_statuses_parse(self):
        for status_value in ["replied", "qualified", "escalated", "declined"]:
            data = {"reply": "hi", "status": status_value, "confidence": 80, "escalate": False}
            _, status, _, _ = _validate_parsed(data)
            assert status == CandidateStatus(status_value)

    def test_missing_field_raises(self):
        data = {"reply": "hi", "status": "replied", "confidence": 90}
        with pytest.raises(ValueError):
            _validate_parsed(data)

    def test_unknown_status_raises(self):
        data = {"reply": "hi", "status": "not_a_real_status", "confidence": 90, "escalate": False}
        with pytest.raises(ValueError):
            _validate_parsed(data)

    def test_non_integer_confidence_raises(self):
        data = {"reply": "hi", "status": "replied", "confidence": "high", "escalate": False}
        with pytest.raises(ValueError):
            _validate_parsed(data)

    def test_confidence_out_of_range_raises(self):
        data = {"reply": "hi", "status": "replied", "confidence": 150, "escalate": False}
        with pytest.raises(ValueError):
            _validate_parsed(data)

    def test_empty_reply_raises(self):
        data = {"reply": "   ", "status": "replied", "confidence": 90, "escalate": False}
        with pytest.raises(ValueError):
            _validate_parsed(data)


# ---------------------------------------------------------------------------
# Recruitment flow (mocked LLM responses)
# ---------------------------------------------------------------------------

class TestRecruitmentFlow:
    def test_step1_initial_greeting_replied(self):
        caller = _make_caller(_json_reply(
            reply="Hi, this is Nisha from Amerisource. Are you currently looking for a new opportunity?",
            status="replied",
            confidence=95,
        ))
        result = process_message("Hi", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step2_current_role_and_experience(self):
        caller = _make_caller(_json_reply(
            reply="Great! What's your current role and total years of experience?",
            confidence=92,
        ))
        result = process_message("Yes still looking", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED

    def test_step3_notice_period(self):
        caller = _make_caller(_json_reply(
            reply="Got it. What's your current notice period?",
            confidence=90,
        ))
        result = process_message("I'm a backend dev with 5 years experience", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED

    def test_step4_ctc_question(self):
        caller = _make_caller(_json_reply(
            reply="Could you also share your current CTC and expected CTC?",
            confidence=90,
        ))
        result = process_message("30 days notice period", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step5_role_description(self):
        caller = _make_caller(_json_reply(
            reply="Thanks! This role is on our cloud team, want to hear more?",
            confidence=90,
        ))
        result = process_message("Current 12L, expected 18L", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED
        assert result.escalated is False

    def test_step6_availability_with_two_slot_options(self):
        caller = _make_caller(_json_reply(
            reply="Would Tuesday 11am or Wednesday 3pm work for a screening call?",
            confidence=93,
        ))
        result = process_message("Yes tell me more", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED

    def test_step7_slot_confirmed_marks_qualified(self):
        caller = _make_caller(_json_reply(
            reply="Great, Tuesday 11am it is. Confirmation coming soon.",
            status="qualified",
            confidence=95,
        ))
        result = process_message("Tuesday 11am works", _api_caller=caller)
        assert result.status == CandidateStatus.QUALIFIED
        assert result.escalated is False

    def test_step8_wrap_up_still_qualified(self):
        caller = _make_caller(_json_reply(
            reply="Perfect, all set. Wishing you the best, talk soon!",
            status="qualified",
            confidence=95,
        ))
        result = process_message("Sounds good, thanks", _api_caller=caller)
        assert result.status == CandidateStatus.QUALIFIED

    def test_candidate_declines(self):
        caller = _make_caller(_json_reply(
            reply="No worries, thanks for letting me know. All the best!",
            status="declined",
            confidence=90,
        ))
        result = process_message("Not interested anymore", _api_caller=caller)
        assert result.status == CandidateStatus.DECLINED
        assert result.escalated is False

    def test_conversation_history_carried_through(self):
        seen_messages = {}

        def caller(messages, api_key, **kwargs):
            seen_messages["messages"] = messages
            return _json_reply()

        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hi, this is Nisha"},
        ]
        process_message("How are you?", conversation_history=history, _api_caller=caller)
        assert seen_messages["messages"][:2] == history
        assert seen_messages["messages"][-1] == {"role": "user", "content": "How are you?"}


# ---------------------------------------------------------------------------
# Escalation scenarios
# ---------------------------------------------------------------------------

class TestEscalationScenarios:
    def test_bot_question_triggers_escalation_only_if_llm_flags_it(self):
        caller = _make_caller(_json_reply(
            reply=(
                "I'm Nisha, the recruiting assistant for Amerisource. "
                "I help with the initial screening process."
            ),
            confidence=90,
            escalate=False,
        ))
        result = process_message("Are you a bot?", _api_caller=caller)
        assert result.escalated is False

    def test_salary_question_does_not_escalate(self):
        caller = _make_caller(_json_reply(
            reply="Compensation details will be finalized by the hiring manager.",
            confidence=90,
            escalate=False,
        ))
        result = process_message("What's the salary for this role?", _api_caller=caller)
        assert result.escalated is False
        assert result.status != CandidateStatus.ESCALATED

    def test_visa_question_triggers_escalation(self):
        caller = _make_caller(_json_reply(
            reply="Let me get someone to help with that.",
            confidence=90,
            escalate=True,
        ))
        result = process_message("Do you sponsor a work visa?", _api_caller=caller)
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_low_confidence_triggers_escalation(self):
        caller = _make_caller(_json_reply(
            reply="Not sure I follow, let me check.",
            confidence=CONFIDENCE_THRESHOLD - 1,
            escalate=False,
        ))
        result = process_message("asdkjfh??", _api_caller=caller)
        assert result.escalated is True
        assert result.status == CandidateStatus.ESCALATED

    def test_confidence_at_threshold_is_not_escalated(self):
        caller = _make_caller(_json_reply(
            reply="Sounds good.",
            confidence=CONFIDENCE_THRESHOLD,
            escalate=False,
        ))
        result = process_message("okay", _api_caller=caller)
        assert result.escalated is False

    def test_escalated_draft_reply_preserved_for_human(self):
        caller = _make_caller(_json_reply(
            reply="Draft reply for human review.",
            confidence=90,
            escalate=True,
        ))
        result = process_message("I need visa sponsorship", _api_caller=caller)
        assert result.reply == "Draft reply for human review."
        assert result.escalated is True

    def test_escalated_result_includes_reason(self):
        caller = _make_caller(_json_reply(
            reply="Let me get someone to help with that.",
            confidence=90,
            escalate=True,
        ))
        result = process_message("I want to file a complaint", _api_caller=caller)
        assert result.reason
        assert isinstance(result.reason, str)


# ---------------------------------------------------------------------------
# Already-escalated short-circuit (no LLM call, no auto-reply)
# ---------------------------------------------------------------------------

class TestAlreadyEscalatedShortCircuit:
    def test_llm_is_never_called_once_escalated(self):
        def caller(messages, api_key, **kwargs):
            raise AssertionError("LLM must not be called once escalated")

        result = process_message(
            "Are you even reading my messages?",
            current_status=CandidateStatus.ESCALATED,
            _api_caller=caller,
        )
        assert result.status == CandidateStatus.ESCALATED
        assert result.escalated is True

    def test_reply_is_empty_once_escalated(self):
        result = process_message(
            "Hello???",
            current_status=CandidateStatus.ESCALATED,
            _api_caller=lambda messages, api_key, **kwargs: _json_reply(),
        )
        assert result.reply == ""

    def test_reason_explains_short_circuit(self):
        result = process_message(
            "Following up",
            current_status=CandidateStatus.ESCALATED,
            _api_caller=lambda messages, api_key, **kwargs: _json_reply(),
        )
        assert "already escalated" in result.reason.lower()

    @pytest.mark.parametrize(
        "status",
        [CandidateStatus.REPLIED, CandidateStatus.QUALIFIED, CandidateStatus.DECLINED, None],
    )
    def test_llm_is_called_normally_for_non_escalated_status(self, status):
        calls = []

        def caller(messages, api_key, **kwargs):
            calls.append(messages)
            return _json_reply()

        process_message("Hi again", current_status=status, _api_caller=caller)
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_raises_on_missing_fields(self):
        def caller(messages, api_key, **kwargs):
            return '{"reply": "hi", "status": "replied"}'

        with pytest.raises(ValueError):
            process_message("Hi", _api_caller=caller)

    def test_raises_when_no_api_key_and_no_mock(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError):
            process_message("Hi")

    def test_handles_markdown_fenced_json(self):
        def caller(messages, api_key, **kwargs):
            return "```json\n" + _json_reply() + "\n```"

        result = process_message("Hi", _api_caller=caller)
        assert result.status == CandidateStatus.REPLIED

    def test_raw_content_always_stored(self):
        raw = _json_reply(reply="hello there")

        def caller(messages, api_key, **kwargs):
            return raw

        result = process_message("Hi", _api_caller=caller)
        assert result.raw_content == raw

    def test_retries_once_on_plain_text_then_succeeds(self):
        attempts = {"count": 0}

        def caller(messages, api_key, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return "Sorry, plain text oops."
            return _json_reply()

        result = process_message("Hi", _api_caller=caller)
        assert attempts["count"] == 2
        assert result.status == CandidateStatus.REPLIED


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
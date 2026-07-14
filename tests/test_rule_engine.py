"""
Tests for agent/rule_engine.py

Run with:
    pytest tests/test_rule_engine.py -v
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta,timezone

import pytest

# Add the project root to Python's import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.rule_engine import (
    ESCALATION_KEYWORDS,
    OPT_OUT_KEYWORDS,
    CandidateState,
    RuleAction,
    RuleResult,
    TemplateType,
    check_escalation,
    check_opt_out,
    gate_message,
)
# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def now() -> datetime:
    """A fixed, timezone-aware reference time so window math is deterministic."""
    return datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def within_window(now: datetime, hours: float) -> datetime:
    """Return a timestamp `hours` before `now` (still inside the 24h window)."""
    return now - timedelta(hours=hours)


def outside_window(now: datetime, hours: float) -> datetime:
    """Return a timestamp `hours` before `now` (past the 24h window)."""
    return now - timedelta(hours=hours)


@pytest.fixture
def new_candidate() -> CandidateState:
    """Fixture for a completely new candidate who has never been contacted."""
    return CandidateState(first_outreach_sent_at=None)


@pytest.fixture
def candidate_waiting_for_reply(now: datetime) -> CandidateState:
    """Fixture for a candidate sent an initial message over 24 hours ago who hasn't replied."""
    return CandidateState(
        first_outreach_sent_at=outside_window(now, 25),
        last_candidate_message_at=None,
        no_reply_template_sent=False,
    )


@pytest.fixture
def candidate_waiting_for_final_reminder(now: datetime) -> CandidateState:
    """Fixture for a candidate sent a No Reply template over 24 hours ago who hasn't replied."""
    return CandidateState(
        first_outreach_sent_at=outside_window(now, 50),
        last_template_sent_at=outside_window(now, 25),
        last_candidate_message_at=None,
        no_reply_template_sent=True,
        final_reminder_sent=False,
    )


@pytest.fixture
def candidate_with_active_conversation(now: datetime) -> CandidateState:
    """Fixture for a candidate who replied within the last 24 hours."""
    return CandidateState(
        first_outreach_sent_at=outside_window(now, 10),
        last_candidate_message_at=within_window(now, 2),
        interview_scheduled=False,
    )


@pytest.fixture
def candidate_needing_interview_followup(now: datetime) -> CandidateState:
    """Fixture for a candidate who replied historically, but the 24h reply window has closed."""
    return CandidateState(
        first_outreach_sent_at=outside_window(now, 48),
        last_candidate_message_at=outside_window(now, 26),
        interview_scheduled=False,
    )


@pytest.fixture
def candidate_with_scheduled_interview(now: datetime) -> CandidateState:
    """Fixture for a candidate whose interview is already booked."""
    return CandidateState(
        first_outreach_sent_at=outside_window(now, 24),
        last_candidate_message_at=within_window(now, 1),
        interview_scheduled=True,
    )


# ---------------------------------------------------------------------------
# check_opt_out
# ---------------------------------------------------------------------------

class TestCheckOptOut:
    @pytest.mark.parametrize("keyword", OPT_OUT_KEYWORDS)
    def test_matches_every_opt_out_keyword(self, keyword):
        result = check_opt_out(f"please {keyword} messaging me")
        assert result is not None
        assert result.action == RuleAction.BLOCK_OPT_OUT
        assert result.matched_keyword == keyword

    def test_is_case_insensitive(self):
        result = check_opt_out("STOP contacting me")
        assert result is not None
        assert result.action == RuleAction.BLOCK_OPT_OUT

    def test_ignores_surrounding_whitespace(self):
        result = check_opt_out("   stop   ")
        assert result is not None
        assert result.action == RuleAction.BLOCK_OPT_OUT

    def test_no_match_returns_none(self):
        assert check_opt_out("I'm interested in the role, tell me more") is None

    def test_result_has_no_template_type(self):
        result = check_opt_out("stop")
        assert result.template_type is None


# ---------------------------------------------------------------------------
# check_escalation
# ---------------------------------------------------------------------------

class TestCheckEscalation:
    @pytest.mark.parametrize("keyword", ESCALATION_KEYWORDS)
    def test_matches_every_escalation_keyword(self, keyword):
        result = check_escalation(f"I want to talk about {keyword}")
        assert result is not None
        assert result.action == RuleAction.ESCALATE_TO_HUMAN
        assert result.matched_keyword == keyword

    def test_matches_substring_variants(self):
        assert check_escalation("that felt discriminatory") is not None
        assert check_escalation("I was harassed by the recruiter") is not None

    def test_no_match_returns_none(self):
        assert check_escalation("What's the expected start date?") is None

    def test_salary_terms_are_not_escalated(self):
        assert check_escalation("what's the salary range for this role?") is None
        assert check_escalation("can we discuss compensation and CTC?") is None


# ---------------------------------------------------------------------------
# gate_message: priority ordering
# ---------------------------------------------------------------------------

class TestGateMessagePriority:
    def test_opt_out_beats_final_reminder(self, candidate_waiting_for_final_reminder, now):
        result = gate_message(
            "please stop, do not contact me",
            candidate_state=candidate_waiting_for_final_reminder,
            now=now,
        )
        assert result.action == RuleAction.BLOCK_OPT_OUT
        assert result.template_type is None

    def test_escalation_beats_interview_follow_up(self, candidate_needing_interview_followup, now):
        result = gate_message(
            "I need a visa sponsorship confirmation",
            candidate_state=candidate_needing_interview_followup,
            now=now,
        )
        assert result.action == RuleAction.ESCALATE_TO_HUMAN
        assert result.template_type is None

    def test_interview_scheduled_beats_interview_follow_up(self, now):
        # Even if the last candidate message was a long time ago, if an interview is scheduled, take NO_ACTION
        state = CandidateState(
            first_outreach_sent_at=outside_window(now, 50),
            last_candidate_message_at=outside_window(now, 40),
            interview_scheduled=True,
        )
        result = gate_message("Hello", candidate_state=state, now=now)
        assert result.action == RuleAction.NO_ACTION
        assert result.template_type is None


# ---------------------------------------------------------------------------
# gate_message: state-based verification logic
# ---------------------------------------------------------------------------

class TestGateMessageStateBehavior:
    def test_initial_outreach_returns_job_outreach(self, new_candidate, now):
        result = gate_message("Hi there", candidate_state=new_candidate, now=now)
        assert result.action == RuleAction.SEND_TEMPLATE
        assert result.template_type == TemplateType.JOB_OUTREACH

    def test_no_reply_reminder_triggered_exactly_as_expected(self, candidate_waiting_for_reply, now):
        result = gate_message("Ping", candidate_state=candidate_waiting_for_reply, now=now)
        assert result.action == RuleAction.SEND_TEMPLATE
        assert result.template_type == TemplateType.NO_REPLY

    def test_no_reply_reminder_never_sent_twice(self, now):
        state = CandidateState(
            first_outreach_sent_at=outside_window(now, 30),
            last_candidate_message_at=None,
            no_reply_template_sent=True,
            final_reminder_sent=False,
            last_template_sent_at=outside_window(now, 2),  # Only 2 hours since NO_REPLY template sent
        )
        result = gate_message("Ping", candidate_state=state, now=now)
        assert result.action != TemplateType.NO_REPLY
        assert result.action == RuleAction.PASS_TO_LLM

    def test_final_reminder_triggered_exactly_as_expected(self, candidate_waiting_for_final_reminder, now):
        result = gate_message("Ping", candidate_state=candidate_waiting_for_final_reminder, now=now)
        assert result.action == RuleAction.SEND_TEMPLATE
        assert result.template_type == TemplateType.FINAL_REMINDER

    def test_final_reminder_never_sent_twice(self, now):
        state = CandidateState(
            first_outreach_sent_at=outside_window(now, 60),
            last_template_sent_at=outside_window(now, 30),
            last_candidate_message_at=None,
            no_reply_template_sent=True,
            final_reminder_sent=True,
        )
        result = gate_message("Ping", candidate_state=state, now=now)
        assert result.action == RuleAction.PASS_TO_LLM
        assert result.template_type is None

    def test_interview_followup_triggered_exactly_as_expected(self, candidate_needing_interview_followup, now):
        result = gate_message("Still interested", candidate_state=candidate_needing_interview_followup, now=now)
        assert result.action == RuleAction.SEND_TEMPLATE
        assert result.template_type == TemplateType.INTERVIEW_FOLLOW_UP

    def test_active_conversation_passes_to_llm(self, candidate_with_active_conversation, now):
        result = gate_message("What are the next steps?", candidate_state=candidate_with_active_conversation, now=now)
        assert result.action == RuleAction.PASS_TO_LLM
        assert result.template_type is None

    def test_interview_scheduled_returns_no_action(self, candidate_with_scheduled_interview, now):
        result = gate_message("Can't wait!", candidate_state=candidate_with_scheduled_interview, now=now)
        assert result.action == RuleAction.NO_ACTION
        assert result.template_type is None


# ---------------------------------------------------------------------------
# gate_message: End-to-End Workflow Lifecycles
# ---------------------------------------------------------------------------

class TestEndToEndWorkflowLifecycles:
    def test_complete_candidate_recruitment_lifecycle_flow(self, now):
        # 1. New Candidate State
        state = CandidateState(first_outreach_sent_at=None)
        res1 = gate_message("Cold start", candidate_state=state, now=now)
        assert res1.action == RuleAction.SEND_TEMPLATE
        assert res1.template_type == TemplateType.JOB_OUTREACH

        # Simulate job outreach being sent out
        state.first_outreach_sent_at = now
        state.last_template_sent_at = now

        # 2. 24 Hours elapsed with no reply from candidate
        future_now = now + timedelta(hours=25)
        res2 = gate_message("System check", candidate_state=state, now=future_now)
        assert res2.action == RuleAction.SEND_TEMPLATE
        assert res2.template_type == TemplateType.NO_REPLY

        # Simulate no-reply reminder template being sent out
        state.no_reply_template_sent = True
        state.last_template_sent_at = future_now

        # 3. Another 24 Hours elapsed with no reply from candidate
        distant_now = future_now + timedelta(hours=25)
        res3 = gate_message("System check 2", candidate_state=state, now=distant_now)
        assert res3.action == RuleAction.SEND_TEMPLATE
        assert res3.template_type == TemplateType.FINAL_REMINDER

        # Simulate final reminder template being sent out
        state.final_reminder_sent = True
        state.last_template_sent_at = distant_now

        # 4. Candidate finally replies within the active text window
        candidate_reply_now = distant_now + timedelta(hours=2)
        state.last_candidate_message_at = candidate_reply_now
        res4 = gate_message("I am interested, sorry for the delay", candidate_state=state, now=candidate_reply_now)
        assert res4.action == RuleAction.PASS_TO_LLM

        # 5. Conversation session expires after another 24 hours of inactivity
        session_expiry_now = candidate_reply_now + timedelta(hours=26)
        res5 = gate_message("Checking in", candidate_state=state, now=session_expiry_now)
        assert res5.action == RuleAction.SEND_TEMPLATE
        assert res5.template_type == TemplateType.INTERVIEW_FOLLOW_UP

        # 6. Interview becomes booked and scheduled
        state.interview_scheduled = True
        res6 = gate_message("Confirmed time", candidate_state=state, now=session_expiry_now)
        assert res6.action == RuleAction.NO_ACTION


# ---------------------------------------------------------------------------
# RuleResult.passed
# ---------------------------------------------------------------------------

class TestRuleResultPassed:
    @pytest.mark.parametrize(
        "action,expected",
        [
            (RuleAction.BLOCK_OPT_OUT, False),
            (RuleAction.ESCALATE_TO_HUMAN, False),
            (RuleAction.NO_ACTION, False),
            (RuleAction.SEND_TEMPLATE, False),
            (RuleAction.PASS_TO_LLM, True),
        ],
    )
    def test_passed_only_true_for_pass_to_llm(self, action, expected):
        result = RuleResult(action=action, reason="test")
        assert result.passed is expected
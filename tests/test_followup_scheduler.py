"""
tests/test_followup_scheduler.py

Tests for agent/followup_scheduler.py. Covers the pure timing/
eligibility logic directly (no mocking needed), plus the full
run_followup_cycle() with mocked DB/Twilio calls.
"""
from datetime import datetime, timedelta, timezone

import pytest

import agent.followup_scheduler as scheduler


NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


class TestIsTrackACandidate:
    def test_messaged_status_is_track_a(self):
        assert scheduler._is_track_a_candidate({"status": "messaged"}) is True

    def test_follow_up_sent_status_is_track_a(self):
        assert scheduler._is_track_a_candidate({"status": "follow_up_sent"}) is True

    def test_replied_status_is_not_track_a(self):
        assert scheduler._is_track_a_candidate({"status": "replied"}) is False


class TestDueForNoReplyTemplate:
    def test_due_after_24_hours_with_no_reply(self):
        candidate = {
            "first_outreach_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_1_sent_at": None,
        }
        assert scheduler._due_for_no_reply_template(candidate, NOW) is True

    def test_not_due_before_24_hours(self):
        candidate = {
            "first_outreach_sent_at": (NOW - timedelta(hours=10)).isoformat(),
            "followup_1_sent_at": None,
        }
        assert scheduler._due_for_no_reply_template(candidate, NOW) is False

    def test_not_due_if_already_sent(self):
        candidate = {
            "first_outreach_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_1_sent_at": (NOW - timedelta(hours=1)).isoformat(),
        }
        assert scheduler._due_for_no_reply_template(candidate, NOW) is False

    def test_not_due_if_never_actually_messaged(self):
        candidate = {"first_outreach_sent_at": None, "followup_1_sent_at": None}
        assert scheduler._due_for_no_reply_template(candidate, NOW) is False


class TestDueForFinalReminder:
    def test_due_24_hours_after_no_reply_template(self):
        candidate = {
            "followup_1_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_2_sent_at": None,
        }
        assert scheduler._due_for_final_reminder(candidate, NOW) is True

    def test_not_due_before_no_reply_template_was_ever_sent(self):
        candidate = {"followup_1_sent_at": None, "followup_2_sent_at": None}
        assert scheduler._due_for_final_reminder(candidate, NOW) is False

    def test_not_due_if_already_sent(self):
        candidate = {
            "followup_1_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_2_sent_at": (NOW - timedelta(hours=1)).isoformat(),
        }
        assert scheduler._due_for_final_reminder(candidate, NOW) is False


class TestIsTrackBEligible:
    def test_eligible_when_replied_and_silent_24_hours(self):
        candidate = {
            "status": "replied",
            "interview_followup_sent_at": None,
            "last_candidate_message_at": (NOW - timedelta(hours=25)).isoformat(),
        }
        assert scheduler._is_track_b_eligible(candidate, NOW) is True

    def test_not_eligible_if_not_replied_status(self):
        candidate = {
            "status": "messaged",
            "interview_followup_sent_at": None,
            "last_candidate_message_at": (NOW - timedelta(hours=25)).isoformat(),
        }
        assert scheduler._is_track_b_eligible(candidate, NOW) is False

    def test_not_eligible_if_already_sent(self):
        candidate = {
            "status": "replied",
            "interview_followup_sent_at": (NOW - timedelta(hours=1)).isoformat(),
            "last_candidate_message_at": (NOW - timedelta(hours=25)).isoformat(),
        }
        assert scheduler._is_track_b_eligible(candidate, NOW) is False

    def test_not_eligible_before_24_hours_of_silence(self):
        candidate = {
            "status": "replied",
            "interview_followup_sent_at": None,
            "last_candidate_message_at": (NOW - timedelta(hours=5)).isoformat(),
        }
        assert scheduler._is_track_b_eligible(candidate, NOW) is False


class TestBuildFollowupVariables:
    def test_uses_real_name_and_role(self):
        variables = scheduler._build_followup_variables({"name": "Priya", "role": "NOC"})
        assert variables == {"1": "Priya", "2": "NOC"}

    def test_falls_back_for_placeholder_name(self):
        # Regression guard for the "Hi Unknown" bug in this file too.
        variables = scheduler._build_followup_variables({"name": "Unknown", "role": "NOC"})
        assert variables["1"] == "there"


class TestRunFollowupCycle:
    def _configure_sids(self, monkeypatch):
        monkeypatch.setattr(scheduler, "NO_REPLY_TEMPLATE_SID", "HX-no-reply")
        monkeypatch.setattr(scheduler, "FINAL_REMINDER_TEMPLATE_SID", "HX-final")
        monkeypatch.setattr(scheduler, "INTERVIEW_FOLLOW_UP_TEMPLATE_SID", "HX-interview")

    def test_returns_error_summary_if_template_sids_missing(self, monkeypatch):
        monkeypatch.setattr(scheduler, "NO_REPLY_TEMPLATE_SID", None)
        monkeypatch.setattr(scheduler, "get_candidates_pending_followup", lambda: [])

        result = scheduler.run_followup_cycle()

        assert "error" in result

    def test_sends_no_reply_template_for_eligible_track_a_candidate(self, monkeypatch):
        self._configure_sids(monkeypatch)
        candidate = {
            "id": "cand-1", "name": "Priya", "role": "NOC", "phone": "9876543210",
            "status": "messaged",
            "first_outreach_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_1_sent_at": None, "followup_2_sent_at": None,
            "conversation_history": [],
        }
        monkeypatch.setattr(scheduler, "get_candidates_pending_followup", lambda: [candidate])
        monkeypatch.setattr(scheduler, "update_candidate_metadata", lambda *a, **k: None)

        sent_calls = []
        monkeypatch.setattr(
            scheduler, "send_template_message",
            lambda **kwargs: sent_calls.append(kwargs) or "SM-fake-sid",
        )

        # Freeze "now" so the eligibility check matches the fixture times.
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)

        result = scheduler.run_followup_cycle()

        assert result["sent_no_reply"] == 1
        assert sent_calls[0]["template_sid"] == "HX-no-reply"
        assert sent_calls[0]["template_variables"] == {"1": "Priya", "2": "NOC"}

    def test_records_error_without_crashing_the_whole_cycle(self, monkeypatch):
        self._configure_sids(monkeypatch)
        candidate = {
            "id": "cand-1", "name": "Priya", "role": "NOC", "phone": "9876543210",
            "status": "messaged",
            "first_outreach_sent_at": (NOW - timedelta(hours=25)).isoformat(),
            "followup_1_sent_at": None, "followup_2_sent_at": None,
            "conversation_history": [],
        }
        monkeypatch.setattr(scheduler, "get_candidates_pending_followup", lambda: [candidate])

        from agent.send_functions import MessageSendError
        def failing_send(**kwargs):
            raise MessageSendError("simulated Twilio failure")
        monkeypatch.setattr(scheduler, "send_template_message", failing_send)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)

        result = scheduler.run_followup_cycle()

        assert len(result["errors"]) == 1
        assert result["errors"][0]["candidate_id"] == "cand-1"
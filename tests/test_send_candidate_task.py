"""
tests/test_send_candidate_task.py

Tests for app/tasks.py's send_candidate -- the Celery task that sends
the initial JOB_OUTREACH template to a pending candidate. Covers:
  - skips a candidate who isn't actually "pending" (already sent, or
    doesn't exist)
  - skips gracefully if the template SID isn't configured
  - on success: sends with the right variables, updates status/
    timestamps, and appends the reconstructed message text to
    conversation_history (so it's visible in the dashboard's Messages
    tab even before the candidate replies -- this was a real gap fixed
    earlier)
"""
import pytest

import app.tasks as tasks_module


class FakeSelectChain:
    def __init__(self, row):
        self._row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        class Result:
            data = [self._row] if self._row else []
        return Result()


class FakeUpdateChain:
    def __init__(self, capture):
        self.capture = capture

    def update(self, payload):
        self.capture["update_payload"] = payload
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return None


class FakeSupabase:
    """
    Routes .table("candidates").select(...) to a fixed candidate row,
    and .table("candidates").update(...) to a capturing chain, mirroring
    how send_candidate actually calls each in sequence.
    """
    def __init__(self, row):
        self.row = row
        self.capture = {}

    def table(self, name):
        assert name == "candidates"
        return _FakeTableProxy(self)


class _FakeTableProxy:
    def __init__(self, parent):
        self.parent = parent

    def select(self, *a, **k):
        return FakeSelectChain(self.parent.row)

    def update(self, payload):
        chain = FakeUpdateChain(self.parent.capture)
        return chain.update(payload)


@pytest.fixture
def pending_candidate():
    return {
        "id": "cand-1",
        "name": "Priya",
        "role": "NOC",
        "phone": "9876543210",
        "status": "pending",
        "conversation_history": [],
    }


def test_skips_candidate_not_pending(monkeypatch):
    fake_supabase = FakeSupabase(row=None)  # simulates "no matching pending row"
    monkeypatch.setattr(tasks_module, "supabase", fake_supabase)
    monkeypatch.setattr(tasks_module, "JOB_OUTREACH_TEMPLATE_SID", "HXfake")

    result = tasks_module.send_candidate.run("cand-1")

    assert "not pending" in result


def test_skips_when_template_sid_missing(monkeypatch, pending_candidate):
    fake_supabase = FakeSupabase(row=pending_candidate)
    monkeypatch.setattr(tasks_module, "supabase", fake_supabase)
    monkeypatch.setattr(tasks_module, "JOB_OUTREACH_TEMPLATE_SID", None)

    result = tasks_module.send_candidate.run("cand-1")

    assert "not configured" in result


def test_successful_send_updates_status_and_timestamps(monkeypatch, pending_candidate):
    fake_supabase = FakeSupabase(row=pending_candidate)
    monkeypatch.setattr(tasks_module, "supabase", fake_supabase)
    monkeypatch.setattr(tasks_module, "JOB_OUTREACH_TEMPLATE_SID", "HXfake")
    monkeypatch.setattr(tasks_module, "send_template_message", lambda **kwargs: "SM-fake-sid")

    tasks_module.send_candidate.run("cand-1")

    payload = fake_supabase.capture["update_payload"]
    assert payload["status"] == "messaged"
    assert payload["first_outreach_sent_at"] is not None
    assert payload["last_template_sent_at"] is not None


def test_successful_send_appends_reconstructed_text_to_conversation_history(monkeypatch, pending_candidate):
    # Real gap this guards against: candidates who'd only received
    # outreach (never replied yet) used to be completely invisible in
    # the dashboard's Messages tab, since only LLM-generated replies
    # got added to conversation_history.
    fake_supabase = FakeSupabase(row=pending_candidate)
    monkeypatch.setattr(tasks_module, "supabase", fake_supabase)
    monkeypatch.setattr(tasks_module, "JOB_OUTREACH_TEMPLATE_SID", "HXfake")
    monkeypatch.setattr(tasks_module, "send_template_message", lambda **kwargs: "SM-fake-sid")

    tasks_module.send_candidate.run("cand-1")

    payload = fake_supabase.capture["update_payload"]
    history = payload["conversation_history"]
    assert len(history) == 1
    assert history[0]["role"] == "assistant"
    assert "Priya" in history[0]["content"]
    assert "NOC" in history[0]["content"]


def test_uses_get_safe_display_name_for_unnamed_candidate(monkeypatch, pending_candidate):
    # Regression guard for the "Hi Unknown" bug: an auto-created
    # candidate with the placeholder name should get "there", not the
    # literal placeholder string, in the outbound message.
    pending_candidate["name"] = "Unknown"
    fake_supabase = FakeSupabase(row=pending_candidate)
    monkeypatch.setattr(tasks_module, "supabase", fake_supabase)
    monkeypatch.setattr(tasks_module, "JOB_OUTREACH_TEMPLATE_SID", "HXfake")
    monkeypatch.setattr(tasks_module, "send_template_message", lambda **kwargs: "SM-fake-sid")

    tasks_module.send_candidate.run("cand-1")

    history_text = fake_supabase.capture["update_payload"]["conversation_history"][0]["content"]
    assert "Unknown" not in history_text
    assert "Hi there" in history_text
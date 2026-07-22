"""
tests/test_send_message_endpoint.py

Tests for POST /send-message (app/main.py) -- lets HR send a free-form
WhatsApp reply directly from the dashboard. Covers the real behaviors
that matter for a live system:
  - refuses an empty message
  - refuses a candidate that doesn't exist
  - refuses a candidate who has never replied (no reply window is open)
  - refuses a candidate whose 24-hour free-form reply window has closed
  - succeeds and appends the sent message to conversation_history when
    everything is valid
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture
def client():
    return TestClient(app_main.app)


class FakeSupabaseTable:
    """Minimal fake supporting .select().eq().execute() for one candidate row."""

    def __init__(self, row):
        self._row = row

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        class Result:
            data = [self._row] if self._row else []
        return Result()


class FakeSupabase:
    def __init__(self, row=None):
        self._row = row

    def table(self, name):
        assert name == "candidates"
        return FakeSupabaseTable(self._row)


def test_rejects_empty_message(client, monkeypatch):
    monkeypatch.setattr(app_main, "supabase", FakeSupabase({"id": "1", "phone": "9000000001"}))
    response = client.post("/send-message", json={"candidate_id": "1", "message": "   "})
    assert response.status_code == 400


def test_rejects_unknown_candidate(client, monkeypatch):
    monkeypatch.setattr(app_main, "supabase", FakeSupabase(row=None))
    response = client.post("/send-message", json={"candidate_id": "does-not-exist", "message": "Hi"})
    assert response.status_code == 404


def test_rejects_candidate_who_never_replied(client, monkeypatch):
    # No last_candidate_message_at at all -- no reply window has ever
    # opened for this candidate.
    monkeypatch.setattr(
        app_main, "supabase",
        FakeSupabase({"id": "1", "phone": "9000000001", "last_candidate_message_at": None, "conversation_history": []}),
    )
    response = client.post("/send-message", json={"candidate_id": "1", "message": "Hi"})
    assert response.status_code == 409


def test_rejects_candidate_outside_24_hour_window(client, monkeypatch):
    stale_timestamp = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    monkeypatch.setattr(
        app_main, "supabase",
        FakeSupabase({
            "id": "1", "phone": "9000000001",
            "last_candidate_message_at": stale_timestamp,
            "conversation_history": [],
        }),
    )
    response = client.post("/send-message", json={"candidate_id": "1", "message": "Hi"})
    assert response.status_code == 409


def test_succeeds_within_reply_window_and_appends_to_history(client, monkeypatch):
    recent_timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    candidate_row = {
        "id": "1", "phone": "9000000001",
        "last_candidate_message_at": recent_timestamp,
        "conversation_history": [{"role": "user", "content": "Hi"}],
    }
    monkeypatch.setattr(app_main, "supabase", FakeSupabase(candidate_row))
    monkeypatch.setattr(app_main, "send_text_message", lambda to, body: "SM-fake-sid-123")

    captured = {}
    def fake_append(candidate_id, candidate, role, content):
        captured["candidate_id"] = candidate_id
        captured["role"] = role
        captured["content"] = content
        return (candidate.get("conversation_history") or []) + [{"role": role, "content": content}]
    monkeypatch.setattr(app_main, "append_conversation_turn", fake_append)

    response = client.post("/send-message", json={"candidate_id": "1", "message": "Thanks for your patience!"})

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert response.json()["twilio_message_sid"] == "SM-fake-sid-123"
    assert captured["candidate_id"] == "1"
    assert captured["role"] == "assistant"
    assert captured["content"] == "Thanks for your patience!"
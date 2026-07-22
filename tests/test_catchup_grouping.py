"""
tests/test_catchup_grouping.py

Tests for scripts/catch_up_missed_messages.py's message-grouping logic
-- the fix for a real problem: several messages from the same person
piled up during downtime used to each trigger a separate LLM call and
a separate WhatsApp send, all firing back-to-back within seconds once
the system came back online. From the candidate's side this looked and
felt exactly like spam, and could produce repetitive/contradictory
replies since each call only saw one message in isolation. Grouping
consecutive not-yet-processed messages per phone number into a single
combined message fixes this -- these tests lock the behavior in.

Every external dependency (Twilio client, the pipeline, the on-disk
processed-SIDs state file) is monkeypatched, so this never hits a real
network, database, or leaves stray state files behind.
"""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest


class FakeMessage:
    def __init__(self, sid, from_, to, body, date_sent, direction="inbound"):
        self.sid = sid
        self.from_ = from_
        self.to = to
        self.body = body
        self.date_sent = date_sent
        self.direction = direction


class FakeMessagesResource:
    def __init__(self, messages):
        self._messages = messages

    def list(self, to=None, date_sent_after=None):
        return self._messages


class FakeTwilioClient:
    def __init__(self, messages):
        self.messages = FakeMessagesResource(messages)


@pytest.fixture
def catchup_module(monkeypatch, tmp_path):
    """
    Imports the script fresh with required env vars set and its state
    file redirected to a temp path, so tests never touch the real
    .catchup_processed_sids.json.
    """
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-fake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_WHATSAPP_NUMBER", "+916303298943")

    sys.modules.pop("scripts.catch_up_missed_messages", None)
    import scripts.catch_up_missed_messages as mod
    importlib.reload(mod)

    state_file = tmp_path / "state.json"
    monkeypatch.setattr(mod, "CATCHUP_STATE_FILE", str(state_file))

    return mod


def test_multiple_messages_from_same_phone_are_grouped_into_one_call(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID1", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hi", now - timedelta(minutes=5)),
        FakeMessage("SID2", "whatsapp:+919999900000", "whatsapp:+916303298943", "Are you there?", now - timedelta(minutes=3)),
        FakeMessage("SID3", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hello??", now - timedelta(minutes=1)),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))

    call_count = {"n": 0}
    captured_bodies = []
    def fake_handle(message):
        call_count["n"] += 1
        captured_bodies.append(message.body)
        return {"status": "replied", "reason": "ok"}
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", fake_handle)

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48"])
    catchup_module.main()

    assert call_count["n"] == 1
    assert "Hi" in captured_bodies[0]
    assert "Are you there?" in captured_bodies[0]
    assert "Hello??" in captured_bodies[0]


def test_messages_from_different_phones_are_processed_separately(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID1", "whatsapp:+919999900001", "whatsapp:+916303298943", "Hi", now),
        FakeMessage("SID2", "whatsapp:+919999900002", "whatsapp:+916303298943", "Hello", now),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))

    calls = []
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", lambda m: calls.append(m.from_number) or {"status": "replied", "reason": "ok"})

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48"])
    catchup_module.main()

    assert len(calls) == 2
    assert set(calls) == {"whatsapp:+919999900001", "whatsapp:+919999900002"}


def test_already_processed_sids_are_not_reprocessed(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID-already-done", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hi", now),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))
    with open(catchup_module.CATCHUP_STATE_FILE, "w") as f:
        json.dump(["SID-already-done"], f)

    calls = []
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", lambda m: calls.append(m) or {"status": "replied", "reason": "ok"})

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48"])
    catchup_module.main()

    assert calls == []


def test_outbound_messages_are_ignored(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID1", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hi", now, direction="inbound"),
        FakeMessage("SID2", "whatsapp:+916303298943", "whatsapp:+919999900000", "Hello!", now, direction="outbound-api"),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))

    calls = []
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", lambda m: calls.append(m) or {"status": "replied", "reason": "ok"})

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48"])
    catchup_module.main()

    assert len(calls) == 1


def test_dry_run_does_not_call_handle_candidate_reply(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID1", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hi", now),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))

    calls = []
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", lambda m: calls.append(m) or {"status": "replied", "reason": "ok"})

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48", "--dry-run"])
    catchup_module.main()

    assert calls == []


def test_failed_processing_does_not_mark_sid_as_processed(catchup_module, monkeypatch):
    now = datetime.now(timezone.utc)
    messages = [
        FakeMessage("SID1", "whatsapp:+919999900000", "whatsapp:+916303298943", "Hi", now),
    ]
    monkeypatch.setattr(catchup_module, "Client", lambda *a, **k: FakeTwilioClient(messages))

    def failing_handle(message):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(catchup_module, "handle_candidate_reply", failing_handle)

    monkeypatch.setattr(sys, "argv", ["catch_up_missed_messages.py", "--hours", "48"])
    catchup_module.main()

    saved = catchup_module._load_processed_sids()
    assert "SID1" not in saved
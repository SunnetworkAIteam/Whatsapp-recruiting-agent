"""
tests/test_pipeline_unknown_number_flow.py

Integration tests for agent/pipeline.py's handle_candidate_reply when a
message arrives from a phone number that doesn't match any existing
candidate. Regression guards for the real fixes made to this flow:
  - the message is no longer silently dropped (a real bug: it used to
    return early with "No matching candidate record" and nothing else
    happened)
  - a candidate record is auto-created and the message is passed
    through the normal LLM conversational path (not the cold
    JOB_OUTREACH template)
  - if the LLM captures a real name from the reply, it gets saved to
    the candidate's record

Every external dependency (DB, LLM API, Twilio) is monkeypatched at the
name agent.pipeline imported it under, so this test never hits a real
network or database.
"""
from unittest.mock import MagicMock

import pytest

import agent.pipeline as pipeline
from agent.llm_layer import LLMResult, CandidateStatus
from webhook.schemas import TwilioInboundMessage


def _make_message(body="Hi", from_number="whatsapp:+919999900000"):
    return TwilioInboundMessage(
        MessageSid="SM-test-sid",
        From=from_number,
        To="whatsapp:+916303298943",
        Body=body,
    )


def _fake_new_candidate(phone):
    from datetime import datetime, timezone
    return {
        "id": "auto-created-id",
        "name": "Unknown",
        "phone": phone,
        "role": "Unspecified",
        "status": "replied",
        "conversation_history": [],
        # Must be set (matching what create_candidate_for_unknown_number
        # actually does in production) -- otherwise the REAL rule engine
        # (not mocked in these tests) sees first_outreach_sent_at=None
        # and decides to send the cold JOB_OUTREACH template instead of
        # routing to the LLM conversational path, which is exactly the
        # bug this whole flow was built to avoid.
        "first_outreach_sent_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def patched_pipeline(monkeypatch):
    """
    Patches every external dependency handle_candidate_reply touches,
    with call-tracking so tests can assert on what was actually invoked.
    """
    calls = {}

    monkeypatch.setattr(pipeline, "get_candidate_by_phone", lambda phone: None)

    def fake_create(normalized_phone):
        calls["create_candidate_for_unknown_number"] = normalized_phone
        return _fake_new_candidate(normalized_phone)
    monkeypatch.setattr(pipeline, "create_candidate_for_unknown_number", fake_create)

    monkeypatch.setattr(pipeline, "record_unmatched_message", lambda phone, body: calls.setdefault("record_unmatched_message", (phone, body)))
    monkeypatch.setattr(pipeline, "record_inbound_message", lambda candidate_id: calls.setdefault("record_inbound_message", candidate_id))
    monkeypatch.setattr(pipeline, "transition_status", lambda candidate_id, new_status: calls.setdefault("transition_status", (candidate_id, new_status)))

    def fake_update_metadata(candidate_id, updates):
        calls.setdefault("update_candidate_metadata_calls", []).append((candidate_id, updates))
    monkeypatch.setattr(pipeline, "update_candidate_metadata", fake_update_metadata)

    monkeypatch.setattr(pipeline, "send_text_message", lambda to, body: calls.setdefault("send_text_message", (to, body)) or "SM-fake-reply-sid")

    return calls


def test_unrecognized_number_auto_creates_a_candidate(patched_pipeline, monkeypatch):
    # The LLM asks for the candidate's name, since none is on file yet.
    monkeypatch.setattr(
        pipeline, "process_message",
        lambda **kwargs: LLMResult(
            reply="Hi! I'm Nisha from Amerisource. Could I get your name?",
            status=CandidateStatus.REPLIED,
            confidence=95,
            escalate=False,
            escalated=False,
            reason="Confidence 95 >= threshold 75. Safe to auto-send.",
            raw_content="{}",
            candidate_name=None,
        ),
    )

    result = pipeline.handle_candidate_reply(_make_message(body="Hi"))

    assert patched_pipeline["create_candidate_for_unknown_number"] == "9999900000"
    assert result["escalated"] is False
    assert "name" in result["reply"].lower() or result["reply"]  # got a real reply, not a dropped message


def test_unrecognized_number_message_is_never_silently_dropped(patched_pipeline, monkeypatch):
    # Regression guard for the original bug: previously this returned
    # early with reason="No matching candidate record for this phone
    # number." and never called create_candidate_for_unknown_number at
    # all -- meaning the message just vanished.
    monkeypatch.setattr(
        pipeline, "process_message",
        lambda **kwargs: LLMResult(
            reply="Hi there!",
            status=CandidateStatus.REPLIED,
            confidence=90,
            escalate=False,
            escalated=False,
            reason="ok",
            raw_content="{}",
            candidate_name=None,
        ),
    )

    pipeline.handle_candidate_reply(_make_message())

    assert "create_candidate_for_unknown_number" in patched_pipeline
    assert "record_unmatched_message" in patched_pipeline  # still kept as an audit log


def test_captured_name_is_saved_to_the_candidate_record(patched_pipeline, monkeypatch):
    # Once the candidate tells the bot their name, it should be
    # persisted -- so future messages, template sends, and the
    # dashboard all show their real name instead of "Unknown".
    monkeypatch.setattr(
        pipeline, "process_message",
        lambda **kwargs: LLMResult(
            reply="Nice to meet you, Karthik!",
            status=CandidateStatus.REPLIED,
            confidence=97,
            escalate=False,
            escalated=False,
            reason="ok",
            raw_content="{}",
            candidate_name="Karthik",
        ),
    )

    pipeline.handle_candidate_reply(_make_message(body="I am Karthik"))

    update_calls = patched_pipeline["update_candidate_metadata_calls"]
    name_updates = [u for cid, u in update_calls if "name" in u]
    assert any(u["name"] == "Karthik" for u in name_updates)


def test_no_name_given_yet_does_not_trigger_a_name_update(patched_pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline, "process_message",
        lambda **kwargs: LLMResult(
            reply="Could I get your name?",
            status=CandidateStatus.REPLIED,
            confidence=95,
            escalate=False,
            escalated=False,
            reason="ok",
            raw_content="{}",
            candidate_name=None,
        ),
    )

    pipeline.handle_candidate_reply(_make_message())

    update_calls = patched_pipeline.get("update_candidate_metadata_calls", [])
    name_updates = [u for cid, u in update_calls if "name" in u]
    assert name_updates == []
"""
Tests for the WhatsApp inbound webhook's idempotency logic.

Signature verification is disabled for these tests (TWILIO_VALIDATE_SIGNATURE=false)
since we're not hitting a real public Twilio-signed URL — see webhook/app.py.
A dedicated test class covers signature verification itself using the real
algorithm in webhook/security.py.
"""

from __future__ import annotations

import os
import threading

import pytest

os.environ["TWILIO_VALIDATE_SIGNATURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from webhook import app as webhook_app_module  # noqa: E402
from webhook import orchestrator  # noqa: E402
from webhook.idempotency import IdempotencyStore  # noqa: E402
from webhook.security import compute_twilio_signature, is_valid_twilio_signature  # noqa: E402

WEBHOOK_URL = "/webhooks/whatsapp/inbound"


@pytest.fixture
def client():
    return TestClient(webhook_app_module.app)


@pytest.fixture(autouse=True)
def reset_state():
    webhook_app_module.idempotency_store.clear()
    orchestrator.reset_message_processor()
    yield
    webhook_app_module.idempotency_store.clear()
    orchestrator.reset_message_processor()


@pytest.fixture
def fake_processor():
    """Replaces the real agent pipeline with a spy so we can count calls."""
    calls = []

    def processor(message):
        calls.append(message)
        return {"status": "processed"}

    orchestrator.set_message_processor(processor)
    return calls


def make_form(message_sid="SM123", from_number="whatsapp:+14155551234", body="Yes I'm interested"):
    return {
        "MessageSid": message_sid,
        "From": from_number,
        "To": "whatsapp:+14155559999",
        "Body": body,
    }


# ---------------------------------------------------------------------------
# Endpoint-level idempotency behavior
# ---------------------------------------------------------------------------


def test_new_message_is_processed_once(client, fake_processor):
    response = client.post(WEBHOOK_URL, data=make_form())

    assert response.status_code == 200
    assert "<Response" in response.text
    assert len(fake_processor) == 1


def test_duplicate_message_sid_is_not_reprocessed(client, fake_processor):
    form = make_form(message_sid="SM-duplicate-1")

    first = client.post(WEBHOOK_URL, data=form)
    second = client.post(WEBHOOK_URL, data=form)

    assert first.status_code == 200
    assert second.status_code == 200
    # The agent pipeline should only ever see this MessageSid once.
    assert len(fake_processor) == 1


def test_duplicate_with_different_body_still_deduped_by_sid(client, fake_processor):
    # Simulates Twilio retrying delivery of the exact same message; the
    # MessageSid is what matters, not the body content.
    first = client.post(WEBHOOK_URL, data=make_form(message_sid="SM-2", body="Yes"))
    second = client.post(WEBHOOK_URL, data=make_form(message_sid="SM-2", body="Yes (retry)"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(fake_processor) == 1
    assert fake_processor[0].body == "Yes"


def test_different_message_sids_are_both_processed(client, fake_processor):
    client.post(WEBHOOK_URL, data=make_form(message_sid="SM-a"))
    client.post(WEBHOOK_URL, data=make_form(message_sid="SM-b"))

    assert len(fake_processor) == 2


def test_triplicate_delivery_only_processed_once(client, fake_processor):
    form = make_form(message_sid="SM-triple")
    for _ in range(3):
        response = client.post(WEBHOOK_URL, data=form)
        assert response.status_code == 200

    assert len(fake_processor) == 1


def test_missing_message_sid_returns_400(client, fake_processor):
    form = make_form()
    del form["MessageSid"]

    response = client.post(WEBHOOK_URL, data=form)

    assert response.status_code == 400
    assert len(fake_processor) == 0


def test_missing_required_field_returns_422(client, fake_processor):
    response = client.post(WEBHOOK_URL, data={"MessageSid": "SM-missing-from"})

    assert response.status_code == 422
    assert len(fake_processor) == 0


def test_concurrent_duplicate_deliveries_only_processed_once(client, fake_processor):
    """
    Fires the same MessageSid from multiple threads at once to exercise the
    race window between "check if already seen" and "run the pipeline".
    Only one thread should get through to the agent.
    """
    form = make_form(message_sid="SM-concurrent")
    responses = []
    lock = threading.Lock()

    def fire():
        resp = client.post(WEBHOOK_URL, data=form)
        with lock:
            responses.append(resp)

    threads = [threading.Thread(target=fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(responses) == 10
    assert all(r.status_code == 200 for r in responses)
    assert len(fake_processor) == 1


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_valid_signature_is_accepted(self, client, fake_processor, monkeypatch):
        monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")
        monkeypatch.setenv("TWILIO_WEBHOOK_URL", f"http://testserver{WEBHOOK_URL}")

        form = make_form(message_sid="SM-sig-valid")
        signature = compute_twilio_signature("test-auth-token", f"http://testserver{WEBHOOK_URL}", form)

        response = client.post(WEBHOOK_URL, data=form, headers={"X-Twilio-Signature": signature})

        assert response.status_code == 200
        assert len(fake_processor) == 1

    def test_invalid_signature_is_rejected(self, client, fake_processor, monkeypatch):
        monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")
        monkeypatch.setenv("TWILIO_WEBHOOK_URL", f"http://testserver{WEBHOOK_URL}")

        form = make_form(message_sid="SM-sig-invalid")

        response = client.post(WEBHOOK_URL, data=form, headers={"X-Twilio-Signature": "not-the-real-signature"})

        assert response.status_code == 403
        assert len(fake_processor) == 0

    def test_missing_signature_header_is_rejected(self, client, fake_processor, monkeypatch):
        monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")

        response = client.post(WEBHOOK_URL, data=make_form(message_sid="SM-sig-missing"))

        assert response.status_code == 403
        assert len(fake_processor) == 0

    def test_signature_helper_matches_known_algorithm(self):
        params = {"MessageSid": "SM123", "Body": "hi"}
        sig = compute_twilio_signature("token", "https://example.com/hook", params)
        assert is_valid_twilio_signature("token", "https://example.com/hook", params, sig)
        assert not is_valid_twilio_signature("token", "https://example.com/hook", params, "wrong")


# ---------------------------------------------------------------------------
# Unit tests for the IdempotencyStore itself
# ---------------------------------------------------------------------------


def test_store_save_if_absent_creates_new_record():
    store = IdempotencyStore()
    record, created = store.save_if_absent("SM1", {"status": "queued"})

    assert created is True
    assert store.get("SM1") is not None
    assert len(store) == 1


def test_store_save_if_absent_returns_existing_on_second_call():
    store = IdempotencyStore()
    first, created_first = store.save_if_absent("SM1", {"attempt": 1})
    second, created_second = store.save_if_absent("SM1", {"attempt": 2})

    assert created_first is True
    assert created_second is False
    assert second is first
    assert second.response_body == {"attempt": 1}


def test_store_ttl_expiry_allows_reprocessing():
    import time

    store = IdempotencyStore(ttl_seconds=0.05)
    store.save_if_absent("SM1", {"attempt": 1})
    time.sleep(0.1)

    assert store.get("SM1") is None
    _, created = store.save_if_absent("SM1", {"attempt": 2})
    assert created is True


def test_store_clear_removes_all_records():
    store = IdempotencyStore()
    store.save_if_absent("SM1", {})
    store.save_if_absent("SM2", {})
    store.clear()

    assert len(store) == 0

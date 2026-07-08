"""
test_pipeline.py

Run with: python -m pytest test_pipeline.py -v
(or: python test_pipeline.py to run without pytest, using the __main__ block)

Proves the orchestration logic in agent/pipeline.py does what it's supposed
to: a normal message gets an automatic reply sent; an escalation-trigger
message gets blocked from sending and marked escalated instead.

Uses the placeholder stub modules (_test_stubs.py) wired in via
agent/rule_engine.py, agent/llm_layer.py, app/status.py, webhook/schemas.py
— replace those with your real files and this test should keep passing
unchanged, since it only calls handle_candidate_reply() and inspects its
return value plus the status log.
"""

import _test_stubs
from agent.pipeline import handle_candidate_reply, send_reply
from webhook.schemas import TwilioInboundMessage


def test_normal_message_sends_automatically():
    _test_stubs._reset_status_log()
    msg = TwilioInboundMessage(from_="candidate_1", body="Hi, when is my interview?")

    result = handle_candidate_reply(msg)

    assert result["escalated"] is False
    assert "reply" in result
    statuses = _test_stubs._get_status_log()
    assert ("candidate_1", "replied") in statuses
    print("PASS: normal message sends automatically and is marked 'replied'")


def test_trigger_message_escalates_and_blocks_send():
    _test_stubs._reset_status_log()
    msg = TwilioInboundMessage(from_="candidate_2", body="The salary band you quoted is wrong.")

    result = handle_candidate_reply(msg)

    assert result["escalated"] is True
    assert "reason" in result
    statuses = _test_stubs._get_status_log()
    assert ("candidate_2", "escalated") in statuses
    assert ("candidate_2", "replied") not in statuses
    print("PASS: trigger message escalates and is never marked 'replied'")


def test_send_reply_mock_mode():
    # No TWILIO_AUTH_TOKEN set in this environment -> mock mode -> no real call.
    result = send_reply("candidate_3", "test message")
    assert result["mock"] is True
    print("PASS: send_reply falls back to mock mode without real Twilio credentials")


if __name__ == "__main__":
    test_normal_message_sends_automatically()
    test_trigger_message_escalates_and_blocks_send()
    test_send_reply_mock_mode()
    print("\nAll pipeline tests passed.")

"""
tests/test_create_candidate_for_unknown_number.py

Regression guards for create_candidate_for_unknown_number
(app/status.py) -- the function that auto-creates a candidate record
for a phone number that messages the business WhatsApp number but
doesn't match any existing candidate. Locks in:
  - the exact placeholder values written (via the shared constants)
  - status="replied", not "messaged" (a real bug: "messaged" made the
    rule engine treat them as needing a cold JOB_OUTREACH send)
  - first_outreach_sent_at is set at creation time (a real bug: leaving
    it null made the rule engine treat them as never having received
    outreach, regardless of status)
"""
from app import status


class MockInsertResult:
    def __init__(self, row):
        self.data = [row]


class MockSupabase:
    """Captures the payload passed to candidates.insert() for inspection."""

    def __init__(self):
        self.inserted_payload = None

    def table(self, name):
        assert name == "candidates"
        return self

    def insert(self, payload):
        self.inserted_payload = payload
        return self

    def execute(self):
        # Simulate what Postgres would return: the inserted row, plus a
        # generated id.
        row = {"id": "generated-id-123", **self.inserted_payload}
        return MockInsertResult(row)


def test_uses_the_shared_name_placeholder_constant(monkeypatch):
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    status.create_candidate_for_unknown_number("9876543210")
    assert mock.inserted_payload["name"] == status.UNASSIGNED_NAME_PLACEHOLDER


def test_uses_the_shared_role_placeholder_constant(monkeypatch):
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    status.create_candidate_for_unknown_number("9876543210")
    assert mock.inserted_payload["role"] == status.UNSPECIFIED_ROLE_PLACEHOLDER


def test_status_is_replied_not_messaged(monkeypatch):
    # Real bug this guards against: "messaged" implies "we've sent
    # outreach, awaiting their first reply" -- but this candidate just
    # messaged US first, so the pipeline should treat it as an ongoing
    # conversation from the start, not a fresh cold-outreach candidate.
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    status.create_candidate_for_unknown_number("9876543210")
    assert mock.inserted_payload["status"] == "replied"


def test_first_outreach_sent_at_is_set(monkeypatch):
    # Real bug this guards against: the rule engine treats
    # first_outreach_sent_at is None as "never sent outreach, send the
    # cold JOB_OUTREACH template" regardless of status -- leaving this
    # null caused a real candidate to receive the cold outreach template
    # as a "reply" to their own first message.
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    result = status.create_candidate_for_unknown_number("9876543210")
    assert result["first_outreach_sent_at"] is not None


def test_phone_is_stored_normalized(monkeypatch):
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    status.create_candidate_for_unknown_number("9876543210")
    assert mock.inserted_payload["phone"] == "9876543210"


def test_platform_is_whatsapp(monkeypatch):
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    status.create_candidate_for_unknown_number("9876543210")
    assert mock.inserted_payload["platform"] == "whatsapp"


def test_returns_the_created_row_with_generated_id(monkeypatch):
    mock = MockSupabase()
    monkeypatch.setattr(status, "supabase", mock)
    result = status.create_candidate_for_unknown_number("9876543210")
    assert result["id"] == "generated-id-123"
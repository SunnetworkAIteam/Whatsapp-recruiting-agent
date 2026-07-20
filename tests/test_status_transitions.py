import pytest
from app import status

class MockResult:
    def __init__(self, status):
        self.data = [{"status": status}]

class MockSupabase:
    def __init__(self, current_status="pending"):
        self.current_status = current_status
        self.updated_status = None

    def table(self, name): return self
    def select(self, *args): return self
    def eq(self, *args): return self
    def execute(self): return MockResult(self.current_status)
    def update(self, data):
        self.updated_status = data["status"]
        return self

def test_valid_transition():
    supabase = MockSupabase("pending")
    status.supabase = supabase
    result = status.transition_status(1, "messaged")
    assert supabase.updated_status == "messaged"
    assert "transitioned" in result

def test_invalid_status():
    supabase = MockSupabase("pending")
    status.supabase = supabase
    with pytest.raises(ValueError):
        status.transition_status(1, "foobar")

def test_declined_can_re_engage():
    # "declined" is not a true dead-end: a candidate who previously
    # declined/opted out can message again and legitimately be moved
    # forward if the LLM/rule engine decides to re-engage them (see
    # app/status.py's ALLOWED_TRANSITIONS comment for the real incident
    # that prompted this).
    supabase = MockSupabase("declined")
    status.supabase = supabase
    result = status.transition_status(1, "qualified")
    assert "declined" in result and "qualified" in result


def test_declined_cannot_transition_to_nonsensical_state():
    # Still shouldn't be able to jump to an earlier funnel stage that
    # doesn't make sense as a "re-engagement" target.
    supabase = MockSupabase("declined")
    status.supabase = supabase
    with pytest.raises(ValueError):
        status.transition_status(1, "pending")

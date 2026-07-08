import pytest
from app.queue import enqueue_pending_candidates

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env before tests run


class FakeSupabase:
    def __init__(self, data):
        self.data = data
    def table(self, name): return self
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def execute(self): return type("Result", (), {"data": self.data})

def test_enqueue_pending_candidates(monkeypatch):
    calls = []
    def fake_delay(id): calls.append(id)

    monkeypatch.setattr("app.tasks.send_candidate.delay", fake_delay)
    monkeypatch.setattr("app.queue.supabase", FakeSupabase([{"id": 1}, {"id": 2}]))

    enqueue_pending_candidates()
    assert calls == [1, 2]

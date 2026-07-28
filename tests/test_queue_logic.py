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
    def fake_apply_async(args, countdown=None): calls.append((args[0], countdown))

    monkeypatch.setattr("app.tasks.send_candidate.apply_async", fake_apply_async)
    monkeypatch.setattr("app.queue.supabase", FakeSupabase([{"id": 1}, {"id": 2}]))

    enqueue_pending_candidates()
    assert calls == [(1, 0), (2, 2)]


def test_enqueue_pending_candidates_staggers_by_2_seconds_each(monkeypatch):
    # Real behavior this guards against: sending every pending candidate
    # at once looked and felt like a mass blast. Each successive
    # candidate should be scheduled 2 seconds later than the last, not
    # all fired simultaneously.
    calls = []
    def fake_apply_async(args, countdown=None): calls.append(countdown)

    monkeypatch.setattr("app.tasks.send_candidate.apply_async", fake_apply_async)
    monkeypatch.setattr("app.queue.supabase", FakeSupabase([{"id": 1}, {"id": 2}, {"id": 3}]))

    enqueue_pending_candidates()
    assert calls == [0, 2, 4]

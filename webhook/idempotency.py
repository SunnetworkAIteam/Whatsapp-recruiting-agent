"""
Idempotency store for inbound webhook deliveries.

Twilio (and most webhook senders) will retry a delivery if your endpoint
doesn't respond quickly or returns a non-2xx status, resending the exact
same MessageSid. Without dedup, a candidate's single WhatsApp reply could
get run through the rule engine + LLM twice, potentially sending two
replies or double-updating their status in the pipeline.

This in-memory implementation is fine for local development and tests.
For staging/production, back it with Redis (`SETNX message_sid`) or a
unique constraint on `message_sid` in Postgres, since:
  - it must survive process restarts, and
  - with Celery/RQ workers running as separate processes, an in-memory
    dict in the FastAPI process won't be visible to workers anyway.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class IdempotencyRecord:
    key: str
    response_body: Any
    created_at: float


class IdempotencyStore:
    def __init__(self, ttl_seconds: Optional[float] = None):
        self._records: Dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            if self._is_expired(record):
                del self._records[key]
                return None
            return record

    def save_if_absent(self, key: str, response_body: Any) -> Tuple[IdempotencyRecord, bool]:
        """
        Atomically reserve `key`. Returns (record, created).
        created=False means someone already claimed this key — the caller
        should skip reprocessing and can reuse record.response_body.
        """
        with self._lock:
            existing = self._records.get(key)
            if existing is not None and not self._is_expired(existing):
                return existing, False

            record = IdempotencyRecord(key=key, response_body=response_body, created_at=time.time())
            self._records[key] = record
            return record, True

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def _is_expired(self, record: IdempotencyRecord) -> bool:
        if self._ttl_seconds is None:
            return False
        return (time.time() - record.created_at) > self._ttl_seconds

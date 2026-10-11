"""Service heartbeat: every few seconds, upsert last-processed event time and a processed
count (plus free-form details) into `service_health`."""

from __future__ import annotations

import time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

INTERVAL_S = 5.0


class Heartbeat:
    def __init__(self, conn: psycopg.Connection, service: str, interval_s: float = INTERVAL_S):
        self.conn = conn
        self.service = service
        self.interval_s = interval_s
        self.processed = 0
        self.last_event_ns: int | None = None
        self.details: dict[str, Any] = {}
        self._last_write = 0.0

    def record(self, count: int = 1, last_event_ns: int | None = None) -> None:
        self.processed += count
        if last_event_ns is not None:
            self.last_event_ns = last_event_ns

    def maybe_write(self, **details: Any) -> None:
        """Write if the interval has passed. Call this from the service's main loop."""
        self.details.update(details)
        now = time.monotonic()
        if now - self._last_write >= self.interval_s:
            self.write()
            self._last_write = now

    def write(self) -> None:
        self.conn.execute(
            "INSERT INTO service_health (service, last_event_ns, processed, details, updated_at) "
            "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (service) DO UPDATE SET "
            "last_event_ns = EXCLUDED.last_event_ns, processed = EXCLUDED.processed, "
            "details = EXCLUDED.details, updated_at = now()",
            (self.service, self.last_event_ns, self.processed, Jsonb(self.details)),
        )

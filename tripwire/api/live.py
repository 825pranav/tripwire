"""Live fan-out: one Kafka consumer thread per API process feeds every WebSocket client.

Book snapshots are throttled per symbol; trades and alerts pass through. Each API process
uses its own consumer group and starts from the latest offset, so it only streams what
happens while clients are connected.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any

import msgspec
from confluent_kafka import Consumer

from tripwire.common.codec import decode
from tripwire.common.events import BookSnapshot, Trade
from tripwire.common.kafka import ALERTS, BOOK_SNAPSHOTS, EXECUTIONS

SNAPSHOT_MIN_INTERVAL_S = 0.25


def to_message(topic: str, value: bytes) -> dict[str, Any] | None:
    if topic == ALERTS:
        return {"kind": "alert", "data": json.loads(value)}
    event = decode(value)
    if isinstance(event, (Trade, BookSnapshot)):
        kind = "trade" if isinstance(event, Trade) else "book"
        return {"kind": kind, "data": msgspec.to_builtins(event)}
    return None


class LiveHub:
    def __init__(self, bootstrap: str) -> None:
        self.bootstrap = bootstrap
        self.clients: set[asyncio.Queue[str]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.forwarded = 0
        self.last_event_ns: int | None = None
        self._last_snapshot: dict[str, float] = {}
        self._stop = threading.Event()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        threading.Thread(target=self._run, name="live-hub", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.clients.discard(q)

    def _broadcast(self, text: str) -> None:
        for q in list(self.clients):
            if q.full():  # slow client: drop its oldest message rather than block everyone
                q.get_nowait()
            q.put_nowait(text)

    def _run(self) -> None:
        consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap,
                "group.id": f"api-live-{uuid.uuid4().hex[:8]}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([BOOK_SNAPSHOTS, EXECUTIONS, ALERTS])
        try:
            while not self._stop.is_set():
                msg = consumer.poll(0.5)
                if msg is None or msg.error() or msg.value() is None:
                    continue
                topic, value = msg.topic(), msg.value()
                assert topic is not None and value is not None
                if topic == BOOK_SNAPSHOTS:
                    key = (msg.key() or b"").decode()
                    now = time.monotonic()
                    if now - self._last_snapshot.get(key, 0.0) < SNAPSHOT_MIN_INTERVAL_S:
                        continue
                    self._last_snapshot[key] = now
                message = to_message(topic, value)
                if message is None:
                    continue
                if message["kind"] != "alert":
                    self.last_event_ns = message["data"]["ts"]
                self.forwarded += 1
                if self.clients and self.loop is not None:
                    self.loop.call_soon_threadsafe(self._broadcast, json.dumps(message))
        finally:
            consumer.close()

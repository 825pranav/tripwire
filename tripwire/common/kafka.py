"""Kafka topics, clients and the per-symbol ordering that the detectors rely on.

Events go to one topic per kind, keyed (and so partitioned) by symbol. Each message
carries two headers set by the simulator: `run` (one value per simulator start) and
`seq`, a counter that is contiguous per symbol across all four event topics. Kafka only
orders messages within a partition, so a consumer reading several topics uses
`SymbolReorderer` to rebuild the exact per-symbol order the exchange produced.
"""

from __future__ import annotations

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from tripwire.common.events import BookSnapshot, Event, OrderAccepted, OrderCancelled, Trade

ORDERS = "orders"
EXECUTIONS = "executions"
CANCELS = "cancels"
BOOK_SNAPSHOTS = "book_snapshots"
ALERTS = "alerts"
EVENT_TOPICS = (ORDERS, EXECUTIONS, CANCELS, BOOK_SNAPSHOTS)
ALL_TOPICS = (*EVENT_TOPICS, ALERTS)
PARTITIONS = 3


def topic_for(event: Event) -> str:
    if isinstance(event, OrderAccepted):
        return ORDERS
    if isinstance(event, Trade):
        return EXECUTIONS
    if isinstance(event, OrderCancelled):
        return CANCELS
    assert isinstance(event, BookSnapshot)
    return BOOK_SNAPSHOTS


def make_producer(bootstrap: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap,
            "linger.ms": 5,
            "compression.type": "lz4",
            "enable.idempotence": True,
        }
    )


def make_consumer(bootstrap: str, group: str, offset_reset: str = "latest") -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group,
            "auto.offset.reset": offset_reset,
            "enable.auto.commit": True,
        }
    )


def ensure_topics(bootstrap: str, topics: tuple[str, ...] = ALL_TOPICS) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    missing = [
        NewTopic(t, num_partitions=PARTITIONS, replication_factor=1)
        for t in topics
        if t not in existing
    ]
    for topic, future in admin.create_topics(missing).items() if missing else ():
        try:
            future.result()
        except Exception as exc:  # another service created it first
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise RuntimeError(f"could not create topic {topic}") from exc


Headers = list[tuple[str, str | bytes | None]] | dict[str, str | bytes | None] | None


def header(headers: Headers, name: str) -> bytes | None:
    items = headers.items() if isinstance(headers, dict) else headers or ()
    for key, value in items:
        if key == name:
            return value if isinstance(value, bytes) or value is None else value.encode()
    return None


class SymbolReorderer:
    """Releases events in per-symbol `seq` order, buffering any that arrive early.

    `run` is the simulator's start time, so runs compare numerically. The first message
    seen for a run sets where that symbol's stream starts (a consumer can join mid-run);
    a newer run (the simulator restarted) starts over, and messages from older runs that
    are still draining from other topics are dropped. If the
    buffer for a symbol grows past `max_pending` (a message that will never come), it
    skips ahead to the oldest buffered event rather than stalling.
    """

    def __init__(self, max_pending: int = 10_000) -> None:
        self.max_pending = max_pending
        self.expected: dict[str, int] = {}
        self.run: dict[str, int] = {}
        self.pending: dict[str, dict[int, Event]] = {}

    def push(self, symbol: str, run: bytes, seq: int, event: Event) -> list[Event]:
        """Add one event; return every event that is now ready, in order."""
        run_id = int(run)
        current = self.run.get(symbol)
        if current is not None and run_id < current:
            return []
        if run_id != current:
            self.run[symbol] = run_id
            self.expected[symbol] = seq
            self.pending[symbol] = {}
        expected = self.expected[symbol]
        if seq < expected:
            return []  # duplicate, or older than where this consumer joined
        pending = self.pending[symbol]
        pending[seq] = event
        if len(pending) > self.max_pending:
            expected = min(pending)
        ready = []
        while expected in pending:
            ready.append(pending.pop(expected))
            expected += 1
        self.expected[symbol] = expected
        return ready

    def buffered(self) -> int:
        return sum(len(p) for p in self.pending.values())

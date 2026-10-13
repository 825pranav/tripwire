"""Detector service: a thin Kafka runner around the pure detectors and the ML scorer.

Consumes the four event topics and feeds them through the `DetectionPipeline` (per-symbol
reordering, rule suite, ML scorer), stores events and alerts in Postgres, then publishes
the alerts to `alerts` (after they are committed, so readers always find them stored).
"""

from __future__ import annotations

import time

import msgspec

from tripwire.common.alerts import Alert
from tripwire.common.config import load_settings
from tripwire.common.db import EventWriter, apply_schema, connect
from tripwire.common.kafka import (
    ALERTS,
    EVENT_TOPICS,
    ensure_topics,
    make_consumer,
    make_producer,
)
from tripwire.common.logs import setup_logging
from tripwire.common.reference import load_reference
from tripwire.detectors.pipeline import DetectionPipeline
from tripwire.ml.model import load_model
from tripwire.ops.heartbeat import Heartbeat

FLUSH_INTERVAL_S = 0.5
GROUP = "detectors"


def main() -> None:
    log = setup_logging("detectors")
    settings = load_settings()
    ensure_topics(settings.kafka_bootstrap)
    conn = connect(settings.database_url)
    apply_schema(conn)
    heartbeat = Heartbeat(conn, GROUP)
    writer = EventWriter(conn)
    pipeline = DetectionPipeline(load_reference(), load_model())
    consumer = make_consumer(settings.kafka_bootstrap, GROUP)
    consumer.subscribe(list(EVENT_TOPICS))
    producer = make_producer(settings.kafka_bootstrap)
    encoder = msgspec.json.Encoder()

    outbox: list[Alert] = []
    alert_count = 0
    last_flush = time.monotonic()
    log.info("consuming", extra={"topics": EVENT_TOPICS})
    while True:
        for msg in consumer.consume(num_messages=500, timeout=0.05):
            if msg.error():
                log.warning("kafka error", extra={"error": str(msg.error())})
                continue
            released, alerts = pipeline.handle(msg.value(), msg.headers())
            outbox.extend(alerts)
            for ready in released:
                writer.add(ready)
            if released:
                heartbeat.record(len(released), released[-1].ts)

        if writer.pending() and (
            outbox or writer.pending() > 5_000 or time.monotonic() - last_flush > FLUSH_INTERVAL_S
        ):
            for alert in outbox:
                writer.add_alert(alert)
            writer.flush()
            last_flush = time.monotonic()
            for alert in outbox:
                producer.produce(ALERTS, key=alert.symbol, value=encoder.encode(alert))
                log.info(
                    "alert",
                    extra={"alert_id": alert.alert_id, "type": alert.type, "score": alert.score},
                )
            alert_count += len(outbox)
            outbox.clear()
        producer.poll(0)
        heartbeat.maybe_write(alerts=alert_count, reorder_buffer=pipeline.reorderer.buffered())


if __name__ == "__main__":
    main()

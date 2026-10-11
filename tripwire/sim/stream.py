"""Simulator service: plays seeded trading days in realtime onto Kafka.

    python -m tripwire.sim.stream          # env: SIM_SEED, SIM_SPEED, KAFKA_BOOTSTRAP, ...

Each event goes to its topic keyed by symbol, with `run`/`seq` headers so consumers can
restore exact per-symbol order (see `tripwire.common.kafka`). Days continue after the
last one already stored, so restarts never reuse order ids. Labels still go only to the
local label files.
"""

from __future__ import annotations

import time

from tripwire.common.codec import encode
from tripwire.common.config import load_settings
from tripwire.common.db import apply_schema, connect
from tripwire.common.events import Event
from tripwire.common.kafka import ensure_topics, make_producer, topic_for
from tripwire.common.logs import setup_logging
from tripwire.common.reference import write_reference
from tripwire.ops.heartbeat import Heartbeat
from tripwire.sim.config import SimConfig
from tripwire.sim.core import ID_STRIDE
from tripwire.sim.day import simulate_day
from tripwire.sim.labels import labels_path, write_labels
from tripwire.sim.population import build_population, reference_data


def main() -> None:
    log = setup_logging("sim")
    settings = load_settings()
    ensure_topics(settings.kafka_bootstrap)
    conn = connect(settings.database_url)
    apply_schema(conn)
    heartbeat = Heartbeat(conn, "sim")
    producer = make_producer(settings.kafka_bootstrap)

    config = SimConfig()
    population = build_population(config)
    write_reference(reference_data(config, population))
    row = conn.execute("SELECT max(order_id) FROM orders").fetchone()
    day = (row[0] // ID_STRIDE + 1) if row and row[0] else 0
    run = str(time.time_ns()).encode()
    seq: dict[str, int] = {}
    started: list[int] = []  # event time of this run's first event

    def sink(event: Event) -> None:
        if not started:
            started.append(event.ts)
        n = seq.get(event.symbol, 0)
        seq[event.symbol] = n + 1
        producer.produce(
            topic_for(event),
            key=event.symbol,
            value=encode(event),
            headers=[("run", run), ("seq", str(n).encode())],
        )
        producer.poll(0)
        heartbeat.record(1, event.ts)
        heartbeat.maybe_write(day=day, speed=settings.sim_speed, run_started_ns=started[0])

    log.info("starting", extra={"seed": settings.sim_seed, "speed": settings.sim_speed, "day": day})
    while True:
        seed = settings.sim_seed * 100_000 + day
        result = simulate_day(config, seed, day, sink, settings.sim_speed, population)
        producer.flush()
        write_labels(labels_path(seed, day), result.episodes)
        log.info("session finished", extra={"day": day, "events": result.event_count})
        day += 1


if __name__ == "__main__":
    main()

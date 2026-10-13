"""Benchmarks: `python -m tripwire.eval.bench [--label NAME] [--no-kafka]`.

1. Engine: replay the order flow of a recorded simulated day into a fresh matching engine
   (submits and cancels only; snapshots excluded) and count output events per second.
2. Detectors in-process: the rule suite, the ML scorer, and both together, over the full
   recorded day (best of N runs).
3. End to end through Kafka: a separate producer process publishes the recorded day to
   the event topics; this process consumes them through the same `DetectionPipeline` as
   the detector service (minus Postgres). Throughput at max rate, and alert latency
   (Kafka create time of the triggering event -> alert ready) at a paced rate.

Results go to eval/results/bench.json (+ .md) and are appended to bench_history.jsonl.
Timed sections pin the process to one CPU (see `tripwire.ops.affinity`).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import statistics
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from confluent_kafka import Consumer, TopicPartition

from tripwire.common.codec import encode
from tripwire.common.config import load_settings
from tripwire.common.events import CancelReason, Event, OrderAccepted, OrderCancelled, OrderType
from tripwire.common.kafka import EVENT_TOPICS, ensure_topics, make_producer, topic_for
from tripwire.detectors.pipeline import DetectionPipeline
from tripwire.detectors.suite import DetectorSuite
from tripwire.eval.report import RESULTS_DIR
from tripwire.exchange.engine import MatchingEngine
from tripwire.ml.model import load_model
from tripwire.ml.scorer import MLScorer
from tripwire.ops.affinity import pin_to_cpu
from tripwire.sim.config import SimConfig
from tripwire.sim.core import ID_STRIDE
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data

BENCH_SEED = 3000
BENCH_DAY = 0


def record_day(seed: int = BENCH_SEED) -> list[Event]:
    events: list[Event] = []
    simulate_day(SimConfig(), seed, BENCH_DAY, events.append)
    return events


def best_of(n: int, fn: Callable[[], int]) -> tuple[float, int]:
    """(best seconds, items) over n runs."""
    best, items = float("inf"), 0
    for _ in range(n):
        started = time.perf_counter()
        items = fn()
        best = min(best, time.perf_counter() - started)
    return best, items


def bench_engine(events: list[Event], repeats: int) -> dict[str, Any]:
    symbols = sorted({e.symbol for e in events})
    commands = [
        e
        for e in events
        if isinstance(e, OrderAccepted)
        or (isinstance(e, OrderCancelled) and e.reason is CancelReason.USER)
    ]

    def replay() -> int:
        engine = MatchingEngine(symbols, BENCH_DAY * ID_STRIDE + 1, BENCH_DAY * ID_STRIDE + 1)
        produced = 0
        for c in commands:
            if isinstance(c, OrderAccepted):
                if c.order_type is OrderType.LIMIT:
                    assert c.price is not None
                    _, out = engine.submit_limit(c.ts, c.symbol, c.account, c.side, c.price, c.qty)
                else:
                    _, out = engine.submit_market(c.ts, c.symbol, c.account, c.side, c.qty)
            else:
                out = engine.cancel(c.ts, c.symbol, c.order_id)
            produced += len(out)
        return produced

    seconds, produced = best_of(repeats, replay)
    return {
        "commands": len(commands),
        "events_out": produced,
        "seconds": round(seconds, 3),
        "events_per_s": round(produced / seconds),
        "commands_per_s": round(len(commands) / seconds),
    }


def bench_detectors(events: list[Event], repeats: int) -> dict[str, Any]:
    config = SimConfig()
    reference = reference_data(config, build_population(config))
    model = load_model()

    def rules() -> int:
        DetectorSuite(reference).run(events)
        return len(events)

    def ml() -> int:
        MLScorer(model, reference).run(events)
        return len(events)

    def both() -> int:
        suite, scorer = DetectorSuite(reference), MLScorer(model, reference)
        for e in events:
            suite.process(e)
            scorer.process(e)
        return len(events)

    out = {}
    for name, fn in (("rules", rules), ("ml", ml), ("rules_plus_ml", both)):
        seconds, n = best_of(repeats, fn)
        out[name] = {"seconds": round(seconds, 3), "events_per_s": round(n / seconds)}
    return out


def _produce(
    bootstrap: str, payloads: list[tuple[str, str, bytes]], rate: float | None, cpu: int | None
) -> None:
    """Producer process: publish (topic, symbol, value) with run/seq headers."""
    if cpu is not None:
        pin_to_cpu(cpu)  # children inherit the parent's pin; move off the consumer's core
    producer = make_producer(bootstrap)
    run = str(time.time_ns()).encode()
    seq: dict[str, int] = {}
    started = time.perf_counter()
    for i, (topic, symbol, value) in enumerate(payloads):
        if rate is not None:
            lag = i / rate - (time.perf_counter() - started)
            if lag > 0:
                time.sleep(lag)
        n = seq.get(symbol, 0)
        seq[symbol] = n + 1
        while True:
            try:
                producer.produce(
                    topic, key=symbol, value=value, headers=[("run", run), ("seq", str(n).encode())]
                )
                break
            except BufferError:
                producer.poll(0.05)
        producer.poll(0)
    producer.flush()


def bench_kafka(
    events: list[Event], rate: float | None, label: str, cpu: int | None
) -> dict[str, Any]:
    settings = load_settings()
    ensure_topics(settings.kafka_bootstrap)
    config = SimConfig()
    pipeline = DetectionPipeline(reference_data(config, build_population(config)), load_model())
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": f"bench-{uuid.uuid4().hex[:8]}",
            "enable.auto.commit": False,
            "max.partition.fetch.bytes": 65_536,  # same as the detector service
            "fetch.wait.max.ms": 20,
        }
    )
    # Start reading exactly where the topics end now, so only this run is consumed.
    metadata = consumer.list_topics(timeout=10)
    partitions = []
    for topic in EVENT_TOPICS:
        for p in metadata.topics[topic].partitions:
            _, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            partitions.append(TopicPartition(topic, p, high))
    consumer.assign(partitions)

    payloads = [(topic_for(e), e.symbol, encode(e)) for e in events]
    producer = mp.get_context("spawn").Process(
        target=_produce,
        args=(settings.kafka_bootstrap, payloads, rate, None if cpu is None else cpu + 2),
    )
    producer.start()
    processed = 0
    latencies_ms: list[float] = []
    first: float | None = None
    last = time.perf_counter()
    deadline = time.monotonic() + 300
    while processed < len(events) and time.monotonic() < deadline:
        for msg in consumer.consume(num_messages=500, timeout=0.05):
            if msg.error():
                continue
            if first is None:
                first = time.perf_counter()
            released, alerts = pipeline.handle(msg.value(), msg.headers())
            processed += len(released)
            if released:
                last = time.perf_counter()
            if alerts:
                created_ms = msg.timestamp()[1]
                now_ms = time.time() * 1000
                latencies_ms.extend(now_ms - created_ms for _ in alerts)
    elapsed = last - first if first is not None else 0.0
    producer.join()
    consumer.close()
    print(f"  kafka {label}: {processed:,}/{len(events):,} events", flush=True)
    result: dict[str, Any] = {
        "mode": label,
        "target_rate": rate,
        "events": processed,
        "events_missing": len(events) - processed,
        "seconds": round(elapsed, 2),
        "events_per_s": round(processed / elapsed) if elapsed else None,
        "alerts": len(latencies_ms),
    }
    if latencies_ms:
        ordered = sorted(latencies_ms)
        result["alert_latency_ms"] = {
            "p50": round(statistics.median(ordered), 1),
            "p99": round(ordered[min(len(ordered) - 1, int(0.99 * len(ordered)))], 1),
            "max": round(ordered[-1], 1),
        }
    return result


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except OSError:
        return "unknown"


def markdown(r: dict[str, Any]) -> str:
    e, d = r["engine"], r["detectors"]
    lines = [
        f"Bench `{r['label']}` at {r['commit']} ({r['when']}), pinned CPU {r['pinned_cpu']}, "
        f"one simulated day (seed {BENCH_SEED}, {r['day_events']:,} events).",
        "",
        "| stage | events/s |",
        "|---|---|",
        f"| matching engine ({e['commands']:,} replayed commands) | {e['events_per_s']:,} |",
        f"| rule detectors, in-process | {d['rules']['events_per_s']:,} |",
        f"| ML window scorer, in-process | {d['ml']['events_per_s']:,} |",
        f"| rules + ML, in-process | {d['rules_plus_ml']['events_per_s']:,} |",
    ]
    for k in r.get("kafka", []):
        lines.append(f"| end to end through Kafka ({k['mode']}) | {k['events_per_s']:,} |")
    for k in r.get("kafka", []):
        if "alert_latency_ms" in k:
            lat = k["alert_latency_ms"]
            lines.append(
                f"\nAlert latency, {k['mode']} ({k['alerts']} alerts): p50 {lat['p50']} ms, "
                f"p99 {lat['p99']} ms, max {lat['max']} ms (Kafka create time of the "
                "triggering event to alert ready, excluding the Postgres write)."
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tripwire.eval.bench")
    parser.add_argument("--label", default="current")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--no-kafka", action="store_true")
    parser.add_argument("--paced-rate", type=float, default=5_000.0, help="events/s")
    args = parser.parse_args()

    cpu = pin_to_cpu()
    events = record_day()
    results: dict[str, Any] = {
        "label": args.label,
        "commit": git_commit(),
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "pinned_cpu": cpu,
        "day_events": len(events),
        "engine": bench_engine(events, args.repeats),
        "detectors": bench_detectors(events, args.repeats),
    }
    print(json.dumps(results["engine"]), json.dumps(results["detectors"]), flush=True)
    if not args.no_kafka:
        results["kafka"] = [
            bench_kafka(events, None, "max rate", cpu),
            bench_kafka(events, args.paced_rate, f"paced {args.paced_rate:,.0f} events/s", cpu),
        ]
        print(json.dumps(results["kafka"]))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "bench.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / "bench.md").write_text(markdown(results), encoding="utf-8")
    with (RESULTS_DIR / "bench_history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(results) + "\n")
    print(markdown(results))


if __name__ == "__main__":
    main()

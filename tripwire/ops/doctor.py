"""`make doctor`: one pass/fail table for the whole running system.

Run on the host against the compose stack (defaults point at localhost).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Callable
from functools import partial
from typing import Any

import psycopg
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from tripwire.common.config import Settings, load_settings
from tripwire.common.db import SCHEMA_VERSION, schema_version
from tripwire.common.kafka import ALL_TOPICS, EVENT_TOPICS

MAX_LAG = 5_000  # messages per consumer group
HEARTBEAT_MAX_AGE_S = 15
SERVICES = ("sim", "detectors", "investigator", "api")
ALERT_SILENCE_SIM_MIN = 45  # while the sim runs, expect an alert at least this often
UNVERIFIED_MAX_SHARE = 0.5
MINUTE_NS = 60 * 1_000_000_000

Result = tuple[str, str]  # (PASS|FAIL|WARN|SKIP, detail)


def check_kafka_topics(settings: Settings) -> Result:
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    topics = set(admin.list_topics(timeout=5).topics)
    missing = [t for t in ALL_TOPICS if t not in topics]
    return ("FAIL", f"missing {missing}") if missing else ("PASS", f"{len(ALL_TOPICS)} topics")


def consumer_lag(settings: Settings, group: str, topics: tuple[str, ...]) -> int | None:
    consumer = Consumer({"bootstrap.servers": settings.kafka_bootstrap, "group.id": group})
    try:
        metadata = consumer.list_topics(timeout=5)
        partitions = [
            TopicPartition(topic, p) for topic in topics for p in metadata.topics[topic].partitions
        ]
        committed = consumer.committed(partitions, timeout=5)
        if all(tp.offset < 0 for tp in committed):
            return None  # group has not committed anything yet
        lag = 0
        for tp in committed:
            _, high = consumer.get_watermark_offsets(tp, timeout=5)
            lag += high - tp.offset if tp.offset >= 0 else 0
        return lag
    finally:
        consumer.close()


def check_lag(settings: Settings) -> Result:
    lags = {
        "detectors": consumer_lag(settings, "detectors", EVENT_TOPICS),
        "investigator": consumer_lag(settings, "investigator", ("alerts",)),
    }
    detail = ", ".join(f"{g}={'n/a' if v is None else v}" for g, v in lags.items())
    if any(v is not None and v > MAX_LAG for v in lags.values()):
        return "FAIL", f"{detail} (max {MAX_LAG})"
    if all(v is None for v in lags.values()):
        return "WARN", "no committed offsets yet"
    return "PASS", detail


def check_postgres(conn: psycopg.Connection) -> Result:
    version = schema_version(conn)
    if version != SCHEMA_VERSION:
        return "FAIL", f"schema version {version}, code expects {SCHEMA_VERSION}"
    return "PASS", f"schema v{version}"


def check_ollama(settings: Settings) -> Result:
    with urllib.request.urlopen(f"{settings.ollama_host}/api/tags", timeout=5) as resp:
        models = {m["name"] for m in json.load(resp)["models"]}
    wanted = settings.ollama_model
    if wanted in models or f"{wanted}:latest" in models:
        return "PASS", wanted
    return "FAIL", f"{wanted} not pulled (run `ollama pull {wanted}`)"


def heartbeats(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT service, last_event_ns, processed, details, "
        "extract(epoch FROM now() - updated_at) AS age_s FROM service_health"
    ).fetchall()
    return {
        r[0]: {"last_event_ns": r[1], "processed": r[2], "details": r[3], "age_s": r[4]}
        for r in rows
    }


def check_heartbeat(beats: dict[str, dict[str, Any]], service: str) -> Result:
    beat = beats.get(service)
    if beat is None:
        return "FAIL", "no heartbeat"
    age = float(beat["age_s"])
    detail = f"{age:.1f}s ago, processed {beat['processed']:,}"
    return ("PASS" if age < HEARTBEAT_MAX_AGE_S else "FAIL"), detail


def check_alert_flow(conn: psycopg.Connection, beats: dict[str, dict[str, Any]]) -> Result:
    sim = beats.get("sim")
    if sim is None or float(sim["age_s"]) >= HEARTBEAT_MAX_AGE_S or sim["last_event_ns"] is None:
        return "SKIP", "simulator not running"
    sim_now = int(sim["last_event_ns"])
    run_started = int(sim["details"].get("run_started_ns") or sim_now)
    row = conn.execute(
        "SELECT max(emitted_ts_ns) FROM alerts WHERE emitted_ts_ns >= %s", (run_started,)
    ).fetchone()
    last_alert = row[0] if row else None
    if last_alert is None:
        if sim_now - run_started < ALERT_SILENCE_SIM_MIN * MINUTE_NS:
            return "WARN", "no alerts yet (simulation just started)"
        return "FAIL", f"no alerts in {ALERT_SILENCE_SIM_MIN} simulated minutes"
    silence = (sim_now - int(last_alert)) / MINUTE_NS
    status = "PASS" if silence <= ALERT_SILENCE_SIM_MIN else "FAIL"
    return status, f"last alert {silence:.1f} simulated min ago"


def check_queue(settings: Settings, beats: dict[str, dict[str, Any]]) -> Result:
    beat = beats.get("investigator")
    if beat is None:
        return "SKIP", "investigator not running"
    depth = int(beat["details"].get("queue_depth", 0))
    cap = settings.investigator_queue_cap
    return ("PASS" if depth < cap else "FAIL"), f"{depth}/{cap}"


def check_unverified(conn: psycopg.Connection) -> Result:
    row = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE status = 'unverified') FROM case_reports"
    ).fetchone()
    total, unverified = (row[0], row[1]) if row else (0, 0)
    if not total:
        return "SKIP", "no case reports yet"
    share = unverified / total
    status = "PASS" if share <= UNVERIFIED_MAX_SHARE else "FAIL"
    return status, f"{unverified}/{total} unverified ({share:.0%}, max {UNVERIFIED_MAX_SHARE:.0%})"


def run_checks(settings: Settings) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []

    def run(name: str, fn: Callable[[], Result]) -> None:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"[:120]
        results.append((name, status, detail))

    run("kafka: reachable, topics exist", lambda: check_kafka_topics(settings))
    run("kafka: consumer lag", lambda: check_lag(settings))
    try:
        conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5)
    except Exception as exc:
        results.append(("postgres: reachable, schema", "FAIL", str(exc)[:120]))
        run("ollama: reachable, model pulled", lambda: check_ollama(settings))
        return results
    with conn:
        run("postgres: reachable, schema", lambda: check_postgres(conn))
        run("ollama: reachable, model pulled", lambda: check_ollama(settings))
        beats = heartbeats(conn)
        for service in SERVICES:
            run(f"heartbeat: {service}", partial(check_heartbeat, beats, service))
        run("alerts flowing", lambda: check_alert_flow(conn, beats))
        run("investigator queue depth", lambda: check_queue(settings, beats))
        run("unverified report share", lambda: check_unverified(conn))
    return results


def main() -> int:
    results = run_checks(load_settings())
    width = max(len(name) for name, _, _ in results)
    print(f"{'check'.ljust(width)}  status  detail")
    print(f"{'-' * width}  ------  ------")
    for name, status, detail in results:
        print(f"{name.ljust(width)}  {status:<6}  {detail}")
    failed = sum(status == "FAIL" for _, status, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks not failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Prometheus `/metrics`: the same health signals `make doctor` and the status bar show
(dependencies, heartbeats, consumer lag, investigator queue, alert and report counts), in
the text exposition format so Prometheus can scrape them and Grafana can chart them.

Everything is read at scrape time from Postgres, Kafka and the heartbeat table, so the
numbers come from the same source of truth as the doctor. A dependency that is down
drops its metrics from the scrape instead of failing it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector, CollectorRegistry

from tripwire.api import queries
from tripwire.common.config import Settings
from tripwire.common.kafka import ALERTS, EVENT_TOPICS
from tripwire.ops.doctor import consumer_lag

__all__ = ["CONTENT_TYPE_LATEST", "Snapshot", "families", "gather", "render"]

NS_PER_S = 1_000_000_000
LAG_GROUPS = (("detectors", EVENT_TOPICS), ("investigator", (ALERTS,)))


@dataclass
class Snapshot:
    """Plain values read from the running system; `families` turns them into metrics."""

    dependencies: dict[str, bool] = field(default_factory=dict)
    services: list[dict[str, Any]] = field(default_factory=list)
    lag: dict[str, int | None] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)  # type, source, n
    reports: list[dict[str, Any]] = field(default_factory=list)  # status, n, citations, ...
    queue_cap: int = 0
    postgres_ok: bool = True


def gather(settings: Settings, pool: queries.Pool, dependencies: dict[str, bool]) -> Snapshot:
    snap = Snapshot(dependencies=dependencies, queue_cap=settings.investigator_queue_cap)
    if dependencies.get("kafka"):
        for group, topics in LAG_GROUPS:
            try:
                snap.lag[group] = consumer_lag(settings, group, topics)
            except Exception:
                snap.lag[group] = None
    try:
        with pool.conn() as conn:
            snap.services = queries.service_health(conn)
            live = queries.metrics(conn)
        snap.alerts = live["alerts_by_type"]
        snap.reports = live["reports"]
    except Exception:
        snap.postgres_ok = False
    return snap


def families(snap: Snapshot) -> Iterator[Metric]:
    up = GaugeMetricFamily(
        "tripwire_dependency_up",
        "1 if the dependency passes its readiness check",
        labels=["dependency"],
    )
    for name, ok in sorted(snap.dependencies.items()):
        up.add_metric([name], 1.0 if ok else 0.0)
    yield up

    if snap.postgres_ok:
        yield from _service_families(snap.services, snap.queue_cap)
        yield from _count_families(snap.alerts, snap.reports)

    lag = GaugeMetricFamily(
        "tripwire_consumer_lag_messages",
        "Messages behind the end of the log, summed over the group's partitions",
        labels=["group"],
    )
    for group, value in sorted(snap.lag.items()):
        if value is not None:  # None: the group has not committed yet
            lag.add_metric([group], float(value))
    yield lag


def _service_families(services: Iterable[dict[str, Any]], queue_cap: int) -> Iterator[Metric]:
    age = GaugeMetricFamily(
        "tripwire_heartbeat_age_seconds", "Seconds since the service last wrote its heartbeat",
        labels=["service"],
    )  # fmt: skip
    processed = CounterMetricFamily(
        "tripwire_processed_events", "Events the service has processed since it started",
        labels=["service"],
    )  # fmt: skip
    beats = {s["service"]: s for s in services}
    for name, beat in sorted(beats.items()):
        age.add_metric([name], float(beat["age_s"]))
        processed.add_metric([name], float(beat["processed"]))
    yield age
    yield processed

    sim, det = beats.get("sim"), beats.get("detectors")
    if sim and det and sim["last_event_ns"] is not None and det["last_event_ns"] is not None:
        behind = max(0, int(sim["last_event_ns"]) - int(det["last_event_ns"])) / NS_PER_S
        yield GaugeMetricFamily(
            "tripwire_detectors_behind_simulator_seconds",
            "Simulated seconds between the simulator's latest event and the detectors'",
            value=behind,
        )

    investigator = beats.get("investigator")
    if investigator:
        details = investigator["details"] or {}
        yield GaugeMetricFamily(
            "tripwire_investigator_queue_depth", "Alerts waiting for a case report",
            value=float(details.get("queue_depth", 0)),
        )  # fmt: skip
        yield GaugeMetricFamily(
            "tripwire_investigator_queue_capacity", "Queue size at which intake pauses",
            value=float(details.get("queue_cap", queue_cap)),
        )  # fmt: skip

    api = beats.get("api")
    if api:
        yield GaugeMetricFamily(
            "tripwire_websocket_clients", "Dashboards connected to the live feed",
            value=float((api["details"] or {}).get("websocket_clients", 0)),
        )  # fmt: skip


def _count_families(
    alerts: Iterable[dict[str, Any]], reports: Iterable[dict[str, Any]]
) -> Iterator[Metric]:
    alert_count = CounterMetricFamily(
        "tripwire_alerts", "Alerts stored, by abuse type and detector", labels=["type", "source"]
    )
    for row in alerts:
        alert_count.add_metric([row["type"], row["source"]], float(row["n"]))
    yield alert_count

    report_count = CounterMetricFamily(
        "tripwire_case_reports", "LLM case reports stored, by validation status", labels=["status"]
    )
    citations = CounterMetricFamily(
        "tripwire_case_report_citations", "Orders cited in case reports", labels=["status", "valid"]
    )
    latency = GaugeMetricFamily(
        "tripwire_case_report_latency_median_seconds", "Median time to write a case report",
        labels=["status"],
    )  # fmt: skip
    for row in reports:
        status = row["status"]
        report_count.add_metric([status], float(row["n"]))
        total, valid = int(row["citations"] or 0), int(row["valid"] or 0)
        citations.add_metric([status, "true"], float(valid))
        citations.add_metric([status, "false"], float(total - valid))
        if row["median_latency_ms"] is not None:
            latency.add_metric([status], float(row["median_latency_ms"]) / 1000)
    yield report_count
    yield citations
    yield latency


class _Fixed(Collector):
    def __init__(self, metrics: list[Metric]) -> None:
        self.metrics = metrics

    def collect(self) -> Iterable[Metric]:
        return self.metrics


def render(snap: Snapshot) -> bytes:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_Fixed(list(families(snap))))
    return generate_latest(registry)

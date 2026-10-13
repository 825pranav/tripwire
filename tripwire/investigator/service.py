"""Investigator service: consumes `alerts`, writes case reports.

A local LLM takes tens of seconds per report, so alerts go through a bounded queue to
`INVESTIGATOR_CONCURRENCY` worker threads. When the queue is full the consumer pauses
its partitions (backpressure stays in Kafka as consumer lag) and resumes once the queue
drains below half. Detection is a separate service and is never slowed down by this.

Offsets are committed when an alert is polled, so alerts still queued when the service
stops are not redelivered. On startup it therefore also queues every stored alert that has
no case report yet (newest first), and workers skip alerts that already have one.
"""

from __future__ import annotations

import queue
import threading
import time
from logging import Logger

import msgspec
import psycopg
from psycopg.types.json import Jsonb

from tripwire.common.alerts import Alert
from tripwire.common.config import Settings, load_settings
from tripwire.common.db import apply_schema, connect
from tripwire.common.kafka import ALERTS, ensure_topics, make_consumer
from tripwire.common.logs import setup_logging
from tripwire.investigator.agent import CaseResult, OllamaLLM, investigate
from tripwire.investigator.evidence import build_window, fetch_events
from tripwire.ops.heartbeat import Heartbeat
from tripwire.ops.shutdown import exit_on_sigterm

GROUP = "investigator"


def store(conn: psycopg.Connection, alert_id: str, result: CaseResult) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO case_reports (alert_id, status, model, report, regulations, attempts, "
            "citations_total, citations_valid, validation_errors, latency_ms) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (alert_id) DO UPDATE SET "
            "status = EXCLUDED.status, model = EXCLUDED.model, report = EXCLUDED.report, "
            "regulations = EXCLUDED.regulations, attempts = EXCLUDED.attempts, "
            "citations_total = EXCLUDED.citations_total, "
            "citations_valid = EXCLUDED.citations_valid, "
            "validation_errors = EXCLUDED.validation_errors, latency_ms = EXCLUDED.latency_ms, "
            "created_at = now()",
            (
                alert_id,
                result.status,
                result.model,
                Jsonb(msgspec.to_builtins(result.report)),
                Jsonb(
                    {
                        "disclaimer": result.disclaimer,
                        "references": msgspec.to_builtins(result.regulations),
                    }
                ),
                result.attempts,
                result.validation.total,
                result.validation.valid,
                Jsonb(result.validation.errors),
                result.latency_ms,
            ),
        )
        conn.execute("UPDATE alerts SET status = 'reported' WHERE alert_id = %s", (alert_id,))


UNREPORTED_SQL = (
    "SELECT alert_id, type, symbol, accounts, window_start_ns, window_end_ns, order_ids, score, "
    "features, emitted_ts_ns, source FROM alerts a WHERE NOT EXISTS "
    "(SELECT 1 FROM case_reports r WHERE r.alert_id = a.alert_id) ORDER BY emitted_ts_ns DESC"
)


def unreported_alerts(conn: psycopg.Connection) -> list[Alert]:
    return [
        Alert(
            alert_id=r[0], type=r[1], symbol=r[2], accounts=tuple(r[3]), window_start_ns=r[4],
            window_end_ns=r[5], order_ids=tuple(r[6]), score=float(r[7]), features=r[8],
            emitted_ts=r[9], source=r[10],
        )
        for r in conn.execute(UNREPORTED_SQL).fetchall()
    ]  # fmt: skip


def has_report(conn: psycopg.Connection, alert_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM case_reports WHERE alert_id = %s", (alert_id,)).fetchone()
    return row is not None


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reports = 0
        self.by_status: dict[str, int] = {}
        self.citations = 0
        self.valid = 0

    def add(self, result: CaseResult) -> None:
        with self.lock:
            self.reports += 1
            self.by_status[result.status] = self.by_status.get(result.status, 0) + 1
            self.citations += result.validation.total
            self.valid += result.validation.valid


def worker(
    jobs: queue.Queue[Alert], settings: Settings, llm: OllamaLLM, stats: Stats, log: Logger
) -> None:
    conn = connect(settings.database_url)
    while True:
        alert = jobs.get()
        try:
            if has_report(conn, alert.alert_id):
                continue
            window = build_window(alert, fetch_events(conn, alert))
            result = investigate(window, llm)
            store(conn, alert.alert_id, result)
            stats.add(result)
            log.info(
                "case report",
                extra={
                    "alert_id": alert.alert_id,
                    "status": result.status,
                    "citations": f"{result.validation.valid}/{result.validation.total}",
                    "latency_ms": result.latency_ms,
                },
            )
        except Exception:
            log.exception("investigation failed", extra={"alert_id": alert.alert_id})
        finally:
            jobs.task_done()


def main() -> None:
    log = setup_logging(GROUP)
    settings = load_settings()
    ensure_topics(settings.kafka_bootstrap)
    conn = connect(settings.database_url)
    apply_schema(conn)
    heartbeat = Heartbeat(conn, GROUP)
    llm = OllamaLLM(settings.ollama_host, settings.ollama_model)
    stats = Stats()
    jobs: queue.Queue[Alert] = queue.Queue(maxsize=settings.investigator_queue_cap)
    for _ in range(settings.investigator_concurrency):
        threading.Thread(target=worker, args=(jobs, settings, llm, stats, log), daemon=True).start()

    backlog = unreported_alerts(conn)
    if backlog:
        log.info("queueing unreported alerts", extra={"count": len(backlog)})

        def enqueue_backlog() -> None:
            for alert in backlog:
                jobs.put(alert)

        threading.Thread(target=enqueue_backlog, name="backlog", daemon=True).start()

    consumer = make_consumer(settings.kafka_bootstrap, GROUP, offset_reset="earliest")
    consumer.subscribe([ALERTS])
    decoder = msgspec.json.Decoder(Alert)
    paused = False
    ollama_ok = llm.available()
    last_probe = time.monotonic()
    exit_on_sigterm()
    log.info("consuming", extra={"model": settings.ollama_model, "ollama_ok": ollama_ok})
    try:
        while True:
            if not paused and jobs.qsize() >= settings.investigator_queue_cap - 1:
                consumer.pause(consumer.assignment())
                paused = True
            elif paused and jobs.qsize() < settings.investigator_queue_cap // 2:
                consumer.resume(consumer.assignment())
                paused = False
            msg = consumer.poll(0.5)
            if msg is not None and not msg.error() and msg.value() is not None:
                alert = decoder.decode(msg.value())  # type: ignore[arg-type]
                jobs.put(alert)
                heartbeat.record(1, alert.emitted_ts)
            if time.monotonic() - last_probe > 30:
                ollama_ok, last_probe = llm.available(), time.monotonic()
            with stats.lock:
                heartbeat.maybe_write(
                    queue_depth=jobs.qsize(),
                    queue_cap=settings.investigator_queue_cap,
                    paused=paused,
                    reports=stats.reports,
                    by_status=dict(stats.by_status),
                    citation_accuracy=round(stats.valid / stats.citations, 4)
                    if stats.citations
                    else None,
                    ollama_ok=ollama_ok,
                    model=settings.ollama_model,
                )

    finally:
        log.info("stopping")
        consumer.close()


if __name__ == "__main__":
    main()

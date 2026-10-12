"""Read-side SQL for the API. Every function takes a connection and returns plain dicts."""

from __future__ import annotations

import queue
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

PADDING_NS = 30 * 1_000_000_000


class Pool:
    """A few long-lived connections shared by request threads."""

    def __init__(self, url: str, size: int = 4) -> None:
        self.url = url
        self.size = size
        self._idle: queue.Queue[psycopg.Connection[dict[str, Any]]] = queue.Queue()
        self._created = 0

    @contextmanager
    def conn(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        try:
            conn = self._idle.get_nowait()
        except queue.Empty:
            if self._created < self.size:
                conn = psycopg.connect(
                    self.url, autocommit=True, connect_timeout=5, row_factory=dict_row
                )
                self._created += 1
            else:
                conn = self._idle.get(timeout=10)
        try:
            yield conn
        except psycopg.OperationalError:
            conn.close()
            self._created -= 1
            raise
        else:
            self._idle.put(conn)


def list_alerts(
    conn: psycopg.Connection[dict[str, Any]],
    alert_type: str | None,
    symbol: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT a.*, r.status AS report_status FROM alerts a "
        "LEFT JOIN case_reports r USING (alert_id) "
        "WHERE (%(type)s::text IS NULL OR a.type = %(type)s) "
        "AND (%(symbol)s::text IS NULL OR a.symbol = %(symbol)s) "
        "AND (%(status)s::text IS NULL OR a.status = %(status)s) "
        "ORDER BY a.emitted_ts_ns DESC LIMIT %(limit)s",
        {"type": alert_type, "symbol": symbol, "status": status, "limit": limit},
    ).fetchall()


def get_alert(conn: psycopg.Connection[dict[str, Any]], alert_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT a.*, r.status AS report_status FROM alerts a "
        "LEFT JOIN case_reports r USING (alert_id) WHERE alert_id = %s",
        (alert_id,),
    ).fetchone()


def get_report(conn: psycopg.Connection[dict[str, Any]], alert_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM case_reports WHERE alert_id = %s", (alert_id,)).fetchone()


def replay(
    conn: psycopg.Connection[dict[str, Any]],
    symbol: str,
    start_ns: int,
    end_ns: int,
    accounts: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Book snapshots and trades in the window, plus the given accounts' order activity."""
    window = {"symbol": symbol, "start": start_ns, "end": end_ns, "accounts": accounts}
    snapshots = conn.execute(
        "SELECT ts_ns, bids, asks FROM book_snapshots WHERE symbol = %(symbol)s "
        "AND ts_ns BETWEEN %(start)s AND %(end)s ORDER BY ts_ns",
        window,
    ).fetchall()
    trades = conn.execute(
        "SELECT * FROM trades WHERE symbol = %(symbol)s AND ts_ns BETWEEN %(start)s AND %(end)s "
        "ORDER BY ts_ns, trade_id",
        window,
    ).fetchall()
    orders = conn.execute(
        "SELECT * FROM orders WHERE symbol = %(symbol)s AND ts_ns BETWEEN %(start)s AND %(end)s "
        "AND account = ANY(%(accounts)s) ORDER BY ts_ns",
        window,
    ).fetchall()
    cancels = conn.execute(
        "SELECT * FROM cancels WHERE symbol = %(symbol)s AND ts_ns BETWEEN %(start)s AND %(end)s "
        "AND account = ANY(%(accounts)s) ORDER BY ts_ns",
        window,
    ).fetchall()
    return {"snapshots": snapshots, "trades": trades, "orders": orders, "cancels": cancels}


def recent_trades(
    conn: psycopg.Connection[dict[str, Any]], symbol: str, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts_ns, price, qty, aggressor_side, buy_account, sell_account FROM trades "
        "WHERE symbol = %s ORDER BY ts_ns DESC LIMIT %s",
        (symbol, limit),
    ).fetchall()
    return rows[::-1]


def latest_snapshot(conn: psycopg.Connection[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT ts_ns, bids, asks FROM book_snapshots WHERE symbol = %s "
        "ORDER BY ts_ns DESC LIMIT 1",
        (symbol,),
    ).fetchone()


def metrics(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
    by_type = conn.execute(
        "SELECT type, source, count(*) AS n FROM alerts GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    reports = conn.execute(
        "SELECT status, count(*) AS n, sum(citations_total) AS citations, "
        "sum(citations_valid) AS valid, percentile_cont(0.5) WITHIN GROUP "
        "(ORDER BY latency_ms) AS median_latency_ms FROM case_reports GROUP BY status"
    ).fetchall()
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM orders) AS orders, (SELECT count(*) FROM trades) AS trades,"
        " (SELECT count(*) FROM alerts) AS alerts"
    ).fetchone()
    return {"alerts_by_type": by_type, "reports": reports, "totals": counts}


def service_health(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT service, last_event_ns, processed, details, "
        "extract(epoch FROM now() - updated_at)::float AS age_s FROM service_health "
        "ORDER BY service"
    ).fetchall()

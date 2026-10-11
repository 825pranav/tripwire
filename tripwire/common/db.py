"""Postgres access with hand-written SQL (psycopg 3). Schema lives in `sql/schema.sql`."""

from __future__ import annotations

import time
from pathlib import Path

import msgspec
import psycopg
from psycopg.types.json import Jsonb

from tripwire.common.alerts import Alert
from tripwire.common.events import BookSnapshot, Event, OrderAccepted, OrderCancelled, Trade

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


def connect(url: str, retries: int = 30) -> psycopg.Connection:
    """Connect with autocommit, retrying while the database starts up."""
    for attempt in range(retries):
        try:
            return psycopg.connect(url, autocommit=True)
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    raise AssertionError("unreachable")


def apply_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(4242)")  # services may start together
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (SCHEMA_VERSION,),
        )


def schema_version(conn: psycopg.Connection) -> int | None:
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return row[0] if row else None


class EventWriter:
    """Buffers events and alerts and writes them in batches. Idempotent on replays."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.orders: list[tuple[object, ...]] = []
        self.cancels: list[tuple[object, ...]] = []
        self.trades: list[tuple[object, ...]] = []
        self.snapshots: list[tuple[object, ...]] = []
        self.alerts: list[tuple[object, ...]] = []

    def add(self, event: Event) -> None:
        if isinstance(event, OrderAccepted):
            self.orders.append(
                (event.order_id, event.ts, event.symbol, event.account, event.side.value,
                 event.order_type.value, event.price, event.qty)
            )  # fmt: skip
        elif isinstance(event, Trade):
            self.trades.append(
                (event.trade_id, event.ts, event.symbol, event.price, event.qty,
                 event.aggressor_side.value, event.buy_order_id, event.sell_order_id,
                 event.buy_account, event.sell_account)
            )  # fmt: skip
        elif isinstance(event, OrderCancelled):
            self.cancels.append(
                (event.order_id, event.ts, event.symbol, event.account, event.side.value,
                 event.price, event.qty, event.reason.value)
            )  # fmt: skip
        elif isinstance(event, BookSnapshot):
            self.snapshots.append((event.symbol, event.ts, Jsonb(event.bids), Jsonb(event.asks)))

    def add_alert(self, alert: Alert) -> None:
        self.alerts.append(
            (alert.alert_id, alert.type, alert.symbol, list(alert.accounts),
             alert.window_start_ns, alert.window_end_ns, list(alert.order_ids), alert.score,
             Jsonb(alert.features), alert.emitted_ts, alert.source)
        )  # fmt: skip

    def pending(self) -> int:
        return sum(map(len, (self.orders, self.cancels, self.trades, self.snapshots, self.alerts)))

    def flush(self) -> None:
        if not self.pending():
            return
        with self.conn.transaction(), self.conn.cursor() as cur:
            if self.orders:
                cur.executemany(
                    "INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    self.orders,
                )
            if self.trades:
                cur.executemany(
                    "INSERT INTO trades VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    self.trades,
                )
            if self.cancels:
                cur.executemany(
                    "INSERT INTO cancels VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    self.cancels,
                )
            if self.snapshots:
                cur.executemany(
                    "INSERT INTO book_snapshots VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    self.snapshots,
                )
            if self.alerts:
                cur.executemany(
                    "INSERT INTO alerts (alert_id, type, symbol, accounts, window_start_ns, "
                    "window_end_ns, order_ids, score, features, emitted_ts_ns, source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    self.alerts,
                )
        for batch in (self.orders, self.cancels, self.trades, self.snapshots, self.alerts):
            batch.clear()


def alert_from_row(row: dict[str, object]) -> Alert:
    """Rebuild an Alert from an `alerts` table row fetched with dict_row."""
    return msgspec.convert(
        {
            "alert_id": row["alert_id"],
            "type": row["type"],
            "symbol": row["symbol"],
            "accounts": row["accounts"],
            "window_start_ns": row["window_start_ns"],
            "window_end_ns": row["window_end_ns"],
            "order_ids": row["order_ids"],
            "score": row["score"],
            "features": row["features"],
            "emitted_ts": row["emitted_ts_ns"],
            "source": row["source"],
        },
        type=Alert,
    )

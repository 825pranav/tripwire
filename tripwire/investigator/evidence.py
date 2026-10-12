"""The evidence window an investigation is grounded in: the alerted accounts' orders,
trades and cancels in the alert's symbol around the alert window.

Built by one pure function from a list of events, so the streaming service (events read
from Postgres) and the offline citation eval (events in memory) share it.
"""

from __future__ import annotations

from collections.abc import Iterable

import msgspec
import psycopg

from tripwire.common.alerts import Alert
from tripwire.common.events import (
    CancelReason,
    Event,
    OrderAccepted,
    OrderCancelled,
    OrderType,
    Side,
    Trade,
)

PADDING_NS = 30 * 1_000_000_000
MAX_ROWS = 60


class EvidenceRow(msgspec.Struct, frozen=True):
    order_id: int
    event: str  # accepted | cancelled | trade
    t_ms: int
    side: str  # side of the alerted account's order
    qty: int
    price: int | None
    account: str
    detail: str = ""


class EvidenceWindow(msgspec.Struct, frozen=True):
    alert: Alert
    start_ns: int
    rows: list[EvidenceRow]


def build_window(alert: Alert, events: Iterable[Event]) -> EvidenceWindow:
    start = alert.window_start_ns - PADDING_NS
    end = alert.window_end_ns + PADDING_NS
    accounts = set(alert.accounts)
    rows: list[EvidenceRow] = []
    for e in events:
        if e.ts < start or e.ts > end or e.symbol != alert.symbol:
            continue
        t_ms = (e.ts - start) // 1_000_000
        if isinstance(e, OrderAccepted) and e.account in accounts:
            kind = "market" if e.order_type is OrderType.MARKET else "limit"
            rows.append(
                EvidenceRow(
                    e.order_id, "accepted", t_ms, e.side.value, e.qty, e.price, e.account, kind
                )
            )
        elif isinstance(e, OrderCancelled) and e.account in accounts:
            if e.reason is CancelReason.USER:
                rows.append(
                    EvidenceRow(
                        e.order_id, "cancelled", t_ms, e.side.value, e.qty, e.price, e.account
                    )
                )
        elif isinstance(e, Trade):
            for account, order_id, side, other in (
                (e.buy_account, e.buy_order_id, Side.BUY, e.sell_account),
                (e.sell_account, e.sell_order_id, Side.SELL, e.buy_account),
            ):
                if account in accounts:
                    aggressor = "aggressor" if side is e.aggressor_side else "passive"
                    rows.append(
                        EvidenceRow(
                            order_id, "trade", t_ms, side.value, e.qty, e.price, account,
                            f"{aggressor}, counterparty {other}",
                        )
                    )  # fmt: skip
    return EvidenceWindow(alert, start, _prioritise(rows, set(alert.order_ids)))


def _prioritise(rows: list[EvidenceRow], alerted: set[int]) -> list[EvidenceRow]:
    """Keep every row about the alert's own orders, then the largest others; time order."""
    if len(rows) <= MAX_ROWS:
        return rows
    keep = [r for r in rows if r.order_id in alerted]
    others = sorted((r for r in rows if r.order_id not in alerted), key=lambda r: -r.qty)
    chosen = {id(r) for r in keep + others[: max(0, MAX_ROWS - len(keep))]}
    return [r for r in rows if id(r) in chosen]


def fetch_events(conn: psycopg.Connection, alert: Alert) -> list[Event]:
    """Load the window's events for the alerted accounts from Postgres, in event order."""
    start = alert.window_start_ns - PADDING_NS
    end = alert.window_end_ns + PADDING_NS
    accounts = list(alert.accounts)
    window = (alert.symbol, start, end)
    events: list[tuple[int, int, Event]] = []
    orders = conn.execute(
        "SELECT ts_ns, order_id, symbol, account, side, order_type, price, qty FROM orders "
        "WHERE symbol = %s AND ts_ns BETWEEN %s AND %s AND account = ANY(%s)",
        (*window, accounts),
    )
    for ts, oid, sym, account, side, otype, price, qty in orders:
        accepted = OrderAccepted(ts, oid, sym, account, Side(side), OrderType(otype), price, qty)
        events.append((ts, 0, accepted))
    trades = conn.execute(
        "SELECT ts_ns, trade_id, symbol, price, qty, aggressor_side, buy_order_id, "
        "sell_order_id, buy_account, sell_account FROM trades WHERE symbol = %s AND "
        "ts_ns BETWEEN %s AND %s AND (buy_account = ANY(%s) OR sell_account = ANY(%s))",
        (*window, accounts, accounts),
    )
    for ts, tid, sym, price, qty, aggressor, buy_id, sell_id, buyer, seller in trades:
        trade = Trade(ts, tid, sym, price, qty, Side(aggressor), buy_id, sell_id, buyer, seller)
        events.append((ts, 1, trade))
    cancels = conn.execute(
        "SELECT ts_ns, order_id, symbol, account, side, price, qty, reason FROM cancels "
        "WHERE symbol = %s AND ts_ns BETWEEN %s AND %s AND account = ANY(%s)",
        (*window, accounts),
    )
    for ts, oid, sym, account, side, price, qty, reason in cancels:
        cancel = OrderCancelled(ts, oid, sym, account, Side(side), price, qty, CancelReason(reason))
        events.append((ts, 2, cancel))
    events.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in events]

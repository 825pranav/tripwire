"""Market state rebuilt from the event stream and shared by all detectors.

Detectors see this state as it was *before* the current event is applied, so a cancel
can still look up when and how large the cancelled order was.
"""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from tripwire.common.events import BookSnapshot, Event, OrderAccepted, OrderCancelled, Side, Trade

FILL_HORIZON_NS = 2_000_000_000  # how long fills and cancels stay queryable


class Fill(NamedTuple):
    ts: int
    side: Side
    qty: int
    order_id: int


class Cancel(NamedTuple):
    ts: int
    side: Side
    qty: int


class LiveOrder:
    __slots__ = (
        "account",
        "price",
        "qty",
        "side",
        "symbol",
        "ticks_from_touch",
        "touch_qty",
        "ts",
    )

    def __init__(
        self,
        account: str,
        symbol: str,
        side: Side,
        price: int | None,
        qty: int,
        ts: int,
        ticks_from_touch: int | None,
        touch_qty: float,
    ) -> None:
        self.account = account
        self.symbol = symbol
        self.side = side
        self.price = price
        self.qty = qty  # remaining
        self.ts = ts  # accepted at
        self.ticks_from_touch = ticks_from_touch  # vs same-side best at entry; None if unknown
        self.touch_qty = touch_qty  # displayed touch size just before entry


class MarketState:
    __slots__ = ("cancels", "fills", "last_price", "live_qty", "orders", "snapshots")

    def __init__(self) -> None:
        self.orders: dict[int, LiveOrder] = {}
        self.snapshots: dict[str, BookSnapshot] = {}
        self.last_price: dict[str, int] = {}
        self.fills: dict[tuple[str, str], deque[Fill]] = {}
        self.cancels: dict[tuple[str, str], deque[Cancel]] = {}
        # resting quantity per (account, symbol, side)
        self.live_qty: dict[tuple[str, str, Side], int] = {}

    def touch_size(self, symbol: str) -> float:
        """Average displayed quantity at the best bid and ask (latest snapshot)."""
        snap = self.snapshots.get(symbol)
        if snap is None or not snap.bids or not snap.asks:
            return 0.0
        return (snap.bids[0][1] + snap.asks[0][1]) / 2

    def best(self, symbol: str, side: Side) -> int | None:
        snap = self.snapshots.get(symbol)
        if snap is None:
            return None
        levels = snap.bids if side is Side.BUY else snap.asks
        return levels[0][0] if levels else None

    def recent_fills(self, account: str, symbol: str, side: Side, since: int) -> list[Fill]:
        """The account's fills on `side` of `symbol` at or after `since` (event time)."""
        fills = self.fills.get((account, symbol))
        if not fills:
            return []
        return [f for f in fills if f.ts >= since and f.side is side]

    def cancelled_qty(self, account: str, symbol: str, side: Side, since: int) -> int:
        """Quantity the account cancelled on `side` of `symbol` at or after `since`."""
        cancels = self.cancels.get((account, symbol))
        if not cancels:
            return 0
        return sum(c.qty for c in cancels if c.ts >= since and c.side is side)

    def apply(self, event: Event) -> None:
        if isinstance(event, OrderAccepted):
            ticks: int | None = None
            best = self.best(event.symbol, event.side)
            if event.price is not None and best is not None:
                ticks = best - event.price if event.side is Side.BUY else event.price - best
            self.orders[event.order_id] = LiveOrder(
                event.account,
                event.symbol,
                event.side,
                event.price,
                event.qty,
                event.ts,
                ticks,
                self.touch_size(event.symbol),
            )
            if event.price is not None:
                key = (event.account, event.symbol, event.side)
                self.live_qty[key] = self.live_qty.get(key, 0) + event.qty
        elif isinstance(event, Trade):
            self.last_price[event.symbol] = event.price
            self._record_fill(event.buy_account, event, Side.BUY, event.buy_order_id)
            self._record_fill(event.sell_account, event, Side.SELL, event.sell_order_id)
            for order_id in (event.buy_order_id, event.sell_order_id):
                order = self.orders.get(order_id)
                if order is not None:
                    order.qty -= event.qty
                    if order.qty <= 0:
                        del self.orders[order_id]
                    if order.price is not None:
                        self._reduce_live(order, event.qty)
        elif isinstance(event, OrderCancelled):
            order = self.orders.pop(event.order_id, None)
            if order is not None and order.price is not None:
                self._reduce_live(order, event.qty)
            self._record_cancel(event)
        else:
            self.snapshots[event.symbol] = event

    def _record_fill(self, account: str, trade: Trade, side: Side, order_id: int) -> None:
        key = (account, trade.symbol)
        fills = self.fills.get(key)
        if fills is None:
            fills = self.fills[key] = deque()
        fills.append(Fill(trade.ts, side, trade.qty, order_id))
        horizon = trade.ts - FILL_HORIZON_NS
        while fills[0].ts < horizon:
            fills.popleft()

    def _reduce_live(self, order: LiveOrder, qty: int) -> None:
        key = (order.account, order.symbol, order.side)
        self.live_qty[key] = self.live_qty.get(key, 0) - qty

    def _record_cancel(self, event: OrderCancelled) -> None:
        key = (event.account, event.symbol)
        cancels = self.cancels.get(key)
        if cancels is None:
            cancels = self.cancels[key] = deque()
        cancels.append(Cancel(event.ts, event.side, event.qty))
        horizon = event.ts - FILL_HORIZON_NS
        while cancels[0].ts < horizon:
            cancels.popleft()

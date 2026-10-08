"""Layering: a ladder of orders at several price levels on one side is mass-cancelled
right after its owner trades on the other side.

After an account gets a fill, every order it cancels on the opposite side within
`cancel_window_ms` joins a "burst". When the window closes, the burst is flagged if it
1. spans at least `min_levels` distinct prices;
2. totals at least `min_total_qty` shares and `fill_ratio` times the fill; and
3. its nearest order sat within `max_ticks_from_touch` ticks of the touch, and every
   order was resting before the fill.

Market makers cancel two levels per side on a requote, so `min_levels = 3` and the size
conditions keep their cancel/replace churn out.
"""

from __future__ import annotations

from typing import NamedTuple

from tripwire.common.alerts import Alert, clamp01, make_alert_id
from tripwire.common.events import CancelReason, Event, OrderCancelled, Side
from tripwire.detectors.base import MS, NO_ALERTS
from tripwire.detectors.config import LayeringConfig
from tripwire.detectors.state import Fill, MarketState


class _Cancel(NamedTuple):
    order_id: int
    price: int
    qty: int
    ticks_from_touch: int | None
    accepted_ts: int
    cancelled_ts: int


class _Burst:
    __slots__ = ("account", "cancels", "deadline", "fills", "side", "start_ns", "symbol")

    def __init__(
        self, account: str, symbol: str, side: Side, fills: list[Fill], deadline: int
    ) -> None:
        self.account = account
        self.symbol = symbol
        self.side = side  # side of the cancelled layers
        self.fills = fills
        self.deadline = deadline
        self.start_ns = fills[0].ts
        self.cancels: list[_Cancel] = []


class LayeringDetector:
    name = "layering"

    def __init__(self, config: LayeringConfig) -> None:
        self.config = config
        self.window_ns = config.cancel_window_ms * MS
        self.pending: dict[tuple[str, str, Side], _Burst] = {}
        self.next_deadline: int | None = None

    def on_event(self, event: Event, state: MarketState) -> list[Alert]:
        alerts = NO_ALERTS
        if self.next_deadline is not None and event.ts > self.next_deadline:
            alerts = self._close_due(event.ts)
        if isinstance(event, OrderCancelled) and event.reason is CancelReason.USER:
            self._collect(event, state)
        return alerts

    def finish(self, ts: int) -> list[Alert]:
        return self._close_due(None)

    def _collect(self, event: OrderCancelled, state: MarketState) -> None:
        order = state.orders.get(event.order_id)
        if order is None or event.price is None:
            return
        key = (event.account, event.symbol, event.side)
        burst = self.pending.get(key)
        if burst is None:
            since = max(order.ts, event.ts - self.window_ns)
            fills = state.recent_fills(event.account, event.symbol, event.side.opposite, since)
            if not fills:
                return
            deadline = fills[-1].ts + self.window_ns
            burst = self.pending[key] = _Burst(
                event.account, event.symbol, event.side, fills, deadline
            )
            if self.next_deadline is None or deadline < self.next_deadline:
                self.next_deadline = deadline
        if order.ts <= burst.start_ns:
            burst.cancels.append(
                _Cancel(
                    event.order_id,
                    event.price,
                    event.qty,
                    order.ticks_from_touch,
                    order.ts,
                    event.ts,
                )
            )

    def _close_due(self, now: int | None) -> list[Alert]:
        alerts: list[Alert] = []
        for key, burst in list(self.pending.items()):
            if now is None or burst.deadline < now:
                del self.pending[key]
                alert = self._evaluate(burst, now if now is not None else burst.deadline)
                if alert is not None:
                    alerts.append(alert)
        self.next_deadline = min((b.deadline for b in self.pending.values()), default=None)
        return alerts

    def _evaluate(self, burst: _Burst, now: int) -> Alert | None:
        cfg = self.config
        cancels = burst.cancels
        levels = {c.price for c in cancels}
        total = sum(c.qty for c in cancels)
        fill_qty = sum(f.qty for f in burst.fills)
        ticks = [c.ticks_from_touch for c in cancels if c.ticks_from_touch is not None]
        if len(levels) < cfg.min_levels or total < cfg.min_total_qty:
            return None
        if total < cfg.fill_ratio * fill_qty or not ticks or min(ticks) > cfg.max_ticks_from_touch:
            return None

        size_ratio = total / fill_qty
        score = clamp01(
            0.4 * min(1.0, len(levels) / (2 * cfg.min_levels))
            + 0.6 * min(1.0, size_ratio / (4 * cfg.fill_ratio))
        )
        order_ids = tuple(c.order_id for c in cancels)
        fill_ids = tuple(dict.fromkeys(f.order_id for f in burst.fills))
        return Alert(
            alert_id=make_alert_id(self.name, burst.symbol, burst.account, burst.start_ns),
            type=self.name,
            symbol=burst.symbol,
            accounts=(burst.account,),
            window_start_ns=min(c.accepted_ts for c in cancels),
            window_end_ns=max(c.cancelled_ts for c in cancels),
            order_ids=order_ids + fill_ids,
            score=round(score, 4),
            features={
                "levels": float(len(levels)),
                "cancelled_qty": float(total),
                "opposite_fill_qty": float(fill_qty),
                "size_ratio": round(size_ratio, 2),
                "nearest_ticks_from_touch": float(min(ticks)),
            },
            emitted_ts=now,
        )

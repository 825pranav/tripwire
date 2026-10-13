"""Spoofing: a large order is cancelled right after its owner trades on the other side.

The rule fires when an account cancels an order that
1. is large: at least `min_large_qty` shares, `touch_multiple` times the displayed size
   at the touch when it was placed, and `fill_ratio` times the opposite-side fill;
2. dwarfs everything the account showed on the other side: at least `min_side_asymmetry`
   times its opposite-side interest (the fill, plus opposite orders still resting or
   cancelled since the fill);
3. was placed at or near the touch (within `max_ticks_from_touch` ticks);
4. was resting when the same account got a fill on the opposite side; and
5. is cancelled within `cancel_window_ms` of that fill and `max_lifetime_s` of placement.

Market makers also pull one side within milliseconds of a fill, and when carrying
inventory they quote more size on the side that works it off. But they quote both sides,
so their displayed interest stays within a small multiple across the book; condition 2
is what separates them from a spoofer's large fake order against a tiny genuine one.
Institutions rest large orders, but for minutes and without a quick opposite fill.
"""

from __future__ import annotations

from tripwire.common.alerts import Alert, clamp01, make_alert_id
from tripwire.common.events import CancelReason, Event, OrderCancelled
from tripwire.detectors.base import MS, NO_ALERTS, SECOND
from tripwire.detectors.config import SpoofingConfig
from tripwire.detectors.state import MarketState


class SpoofingDetector:
    name = "spoofing"
    wants: tuple[type, ...] = (OrderCancelled,)

    def __init__(self, config: SpoofingConfig) -> None:
        self.config = config
        self.window_ns = config.cancel_window_ms * MS
        self.max_lifetime_ns = config.max_lifetime_s * SECOND

    def on_event(self, event: Event, state: MarketState) -> list[Alert]:
        if not isinstance(event, OrderCancelled) or event.reason is not CancelReason.USER:
            return NO_ALERTS
        cfg = self.config
        if event.qty < cfg.min_large_qty:
            return NO_ALERTS
        order = state.orders.get(event.order_id)
        if order is None or order.ticks_from_touch is None:
            return NO_ALERTS
        if order.ticks_from_touch > cfg.max_ticks_from_touch:
            return NO_ALERTS
        lifetime = event.ts - order.ts
        if lifetime > self.max_lifetime_ns:
            return NO_ALERTS
        since = max(order.ts, event.ts - self.window_ns)
        fills = state.recent_fills(event.account, event.symbol, event.side.opposite, since)
        if not fills:
            return NO_ALERTS
        fill_qty = sum(f.qty for f in fills)
        size_ratio = event.qty / fill_qty
        touch_multiple = event.qty / order.touch_qty if order.touch_qty else 1e6
        if size_ratio < cfg.fill_ratio or touch_multiple < cfg.touch_multiple:
            return NO_ALERTS
        opposite = event.side.opposite
        first_fill = fills[0].ts
        opposite_interest = (
            fill_qty
            + max(0, state.live_qty.get((event.account, event.symbol, opposite), 0))
            + state.cancelled_qty(event.account, event.symbol, opposite, first_fill)
        )
        asymmetry = event.qty / opposite_interest
        if asymmetry < cfg.min_side_asymmetry:
            return NO_ALERTS

        latency_ms = (event.ts - fills[-1].ts) / MS
        score = clamp01(
            0.4 * min(1.0, size_ratio / (4 * cfg.fill_ratio))
            + 0.3 * min(1.0, touch_multiple / (4 * cfg.touch_multiple))
            + 0.3 * (1 - latency_ms / cfg.cancel_window_ms)
        )
        return [
            Alert(
                alert_id=make_alert_id(self.name, event.symbol, event.account, event.ts),
                type=self.name,
                symbol=event.symbol,
                accounts=(event.account,),
                window_start_ns=order.ts,
                window_end_ns=event.ts,
                order_ids=(event.order_id, *dict.fromkeys(f.order_id for f in fills)),
                score=round(score, 4),
                features={
                    "cancelled_qty": float(event.qty),
                    "opposite_fill_qty": float(fill_qty),
                    "size_ratio": round(size_ratio, 2),
                    "side_asymmetry": round(asymmetry, 2),
                    "touch_multiple": round(touch_multiple, 2),
                    "cancel_latency_ms": round(latency_ms, 3),
                    "order_lifetime_s": round(lifetime / SECOND, 3),
                    "ticks_from_touch": float(order.ticks_from_touch),
                },
                emitted_ts=event.ts,
            )
        ]

    def finish(self, ts: int) -> list[Alert]:
        return NO_ALERTS

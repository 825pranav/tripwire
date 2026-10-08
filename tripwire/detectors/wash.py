"""Wash trading: two accounts with the same beneficial owner repeatedly trade with each
other, so no change in ownership happens.

A trade counts as a suspicious cross when buyer and seller are different accounts with
the same owner (per the exchange's KYC registry) and the passive leg had rested for at
most `max_resting_ms` before it was hit, i.e. the two legs were placed to meet. The rule
fires when an owner accumulates `min_trades` such crosses in one symbol within
`window_s`; after firing it stays quiet for that owner and symbol for `window_s`.

Related accounts do sometimes cross by accident (an institution's two desks), but then
the passive order has typically been resting for a long time, which the resting-time
condition filters out.
"""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from tripwire.common.alerts import Alert, clamp01, make_alert_id
from tripwire.common.events import Event, Side, Trade
from tripwire.detectors.base import MS, NO_ALERTS, SECOND
from tripwire.detectors.config import WashConfig
from tripwire.detectors.state import MarketState


class _Cross(NamedTuple):
    ts: int
    buy_account: str
    sell_account: str
    buy_order_id: int
    sell_order_id: int
    qty: int
    resting_ms: float


class WashDetector:
    name = "wash"

    def __init__(self, config: WashConfig, owner_of: dict[str, str]) -> None:
        self.config = config
        self.owner_of = owner_of
        self.window_ns = config.window_s * SECOND
        self.crosses: dict[tuple[str, str], deque[_Cross]] = {}
        self.quiet_until: dict[tuple[str, str], int] = {}

    def on_event(self, event: Event, state: MarketState) -> list[Alert]:
        if not isinstance(event, Trade) or event.buy_account == event.sell_account:
            return NO_ALERTS
        owner = self.owner_of.get(event.buy_account)
        if owner is None or owner != self.owner_of.get(event.sell_account):
            return NO_ALERTS
        passive_id = event.sell_order_id if event.aggressor_side is Side.BUY else event.buy_order_id
        passive = state.orders.get(passive_id)
        if passive is None:
            return NO_ALERTS
        resting_ms = (event.ts - passive.ts) / MS
        if resting_ms > self.config.max_resting_ms:
            return NO_ALERTS

        key = (owner, event.symbol)
        crosses = self.crosses.setdefault(key, deque())
        crosses.append(
            _Cross(
                event.ts,
                event.buy_account,
                event.sell_account,
                event.buy_order_id,
                event.sell_order_id,
                event.qty,
                resting_ms,
            )
        )
        while crosses[0].ts < event.ts - self.window_ns:
            crosses.popleft()
        if len(crosses) < self.config.min_trades or event.ts < self.quiet_until.get(key, 0):
            return NO_ALERTS

        self.quiet_until[key] = event.ts + self.window_ns
        accounts = tuple(sorted({a for c in crosses for a in (c.buy_account, c.sell_account)}))
        volume = sum(c.qty for c in crosses)
        mean_resting = sum(c.resting_ms for c in crosses) / len(crosses)
        score = clamp01(
            0.6 * min(1.0, len(crosses) / (3 * self.config.min_trades))
            + 0.4 * (1 - mean_resting / self.config.max_resting_ms)
        )
        order_ids = tuple(
            dict.fromkeys(oid for c in crosses for oid in (c.sell_order_id, c.buy_order_id))
        )
        return [
            Alert(
                alert_id=make_alert_id(self.name, event.symbol, owner, event.ts),
                type=self.name,
                symbol=event.symbol,
                accounts=accounts,
                window_start_ns=crosses[0].ts,
                window_end_ns=event.ts,
                order_ids=order_ids,
                score=round(score, 4),
                features={
                    "same_owner_trades": float(len(crosses)),
                    "same_owner_volume": float(volume),
                    "mean_resting_ms": round(mean_resting, 3),
                },
                emitted_ts=event.ts,
            )
        ]

    def finish(self, ts: int) -> list[Alert]:
        return NO_ALERTS

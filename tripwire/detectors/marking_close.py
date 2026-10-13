"""Marking the close: one account aggressively trades one way in the final minutes of the
session to push the closing price.

For each symbol the rule watches the last `window_s` of the session (from the exchange
calendar). At the close it flags an account whose aggressive (liquidity-taking) volume
in that window
1. is at least `min_aggressive_qty` shares over at least `min_orders` orders;
2. is at least `min_share` of all volume traded in the symbol in the window;
3. is at least `min_one_sided` on one side; and
4. coincides with the price moving `min_impact_ticks` or more in that direction between
   the start of the window and the close.

Legitimate end-of-day rebalancing tends to be spread over a longer period in small
slices, so its share of the final minutes stays below the thresholds.
"""

from __future__ import annotations

from tripwire.common.alerts import Alert, clamp01, make_alert_id
from tripwire.common.events import Event, Side, Trade
from tripwire.common.reference import SessionCalendar
from tripwire.detectors.base import ALL_EVENTS, NO_ALERTS, SECOND
from tripwire.detectors.config import MarkingCloseConfig
from tripwire.detectors.state import MarketState


class _Aggressor:
    __slots__ = ("buy_qty", "first_ts", "order_ids", "sell_qty")

    def __init__(self, ts: int) -> None:
        self.buy_qty = 0
        self.sell_qty = 0
        self.first_ts = ts
        self.order_ids: dict[int, None] = {}  # insertion-ordered set


class _CloseWindow:
    __slots__ = (
        "aggressors",
        "close_ns",
        "last_price",
        "start_ns",
        "start_price",
        "symbol",
        "volume",
    )

    def __init__(self, symbol: str, start_ns: int, close_ns: int, start_price: int) -> None:
        self.symbol = symbol
        self.start_ns = start_ns
        self.close_ns = close_ns
        self.start_price = start_price
        self.last_price = start_price
        self.volume = 0
        self.aggressors: dict[str, _Aggressor] = {}


class MarkingCloseDetector:
    name = "marking_close"
    wants: tuple[type, ...] = ALL_EVENTS

    def __init__(self, config: MarkingCloseConfig, calendar: SessionCalendar) -> None:
        self.config = config
        self.calendar = calendar
        self.window_ns = config.window_s * SECOND
        self.session: tuple[int, int] = (0, -1)  # cached (open, close) of the current day
        self.windows: dict[str, _CloseWindow] = {}

    def on_event(self, event: Event, state: MarketState) -> list[Alert]:
        alerts = NO_ALERTS
        window = self.windows.get(event.symbol)
        if window is not None and event.ts >= window.close_ns:
            alerts = self._close(event.ts, event.symbol)
        if isinstance(event, Trade):
            self._on_trade(event, state)
        return alerts

    def finish(self, ts: int) -> list[Alert]:
        return self._close(ts, None) if self.windows else NO_ALERTS

    def _on_trade(self, trade: Trade, state: MarketState) -> None:
        opened, close = self.session
        if not opened <= trade.ts < close:
            opened, close = self.session = self.calendar.bounds(trade.ts)
        window_start = close - self.window_ns
        if trade.ts < window_start:
            return
        window = self.windows.get(trade.symbol)
        if window is None:
            start_price = state.last_price.get(trade.symbol, trade.price)
            window = _CloseWindow(trade.symbol, window_start, close, start_price)
            self.windows[trade.symbol] = window
        window.volume += trade.qty
        window.last_price = trade.price
        if trade.aggressor_side is Side.BUY:
            account, order_id = trade.buy_account, trade.buy_order_id
        else:
            account, order_id = trade.sell_account, trade.sell_order_id
        aggressor = window.aggressors.get(account)
        if aggressor is None:
            aggressor = window.aggressors[account] = _Aggressor(trade.ts)
        if trade.aggressor_side is Side.BUY:
            aggressor.buy_qty += trade.qty
        else:
            aggressor.sell_qty += trade.qty
        aggressor.order_ids[order_id] = None

    def _close(self, now: int, symbol: str | None) -> list[Alert]:
        """Evaluate the close window of one symbol (or all), per symbol event time."""
        alerts: list[Alert] = []
        for sym in [symbol] if symbol is not None else list(self.windows):
            window = self.windows.pop(sym)
            for account, aggressor in window.aggressors.items():
                alert = self._evaluate(window, account, aggressor, now)
                if alert is not None:
                    alerts.append(alert)
        return alerts

    def _evaluate(
        self, window: _CloseWindow, account: str, agg: _Aggressor, now: int
    ) -> Alert | None:
        cfg = self.config
        total = agg.buy_qty + agg.sell_qty
        if total < cfg.min_aggressive_qty or len(agg.order_ids) < cfg.min_orders:
            return None
        share = total / window.volume
        direction = 1 if agg.buy_qty >= agg.sell_qty else -1
        one_sided = max(agg.buy_qty, agg.sell_qty) / total
        impact = (window.last_price - window.start_price) * direction
        if share < cfg.min_share or one_sided < cfg.min_one_sided:
            return None
        if impact < cfg.min_impact_ticks:
            return None
        score = clamp01(
            0.4 * min(1.0, share / (2 * cfg.min_share))
            + 0.4 * min(1.0, impact / (4 * cfg.min_impact_ticks))
            + 0.2 * one_sided
        )
        return Alert(
            alert_id=make_alert_id(self.name, window.symbol, account, window.close_ns),
            type=self.name,
            symbol=window.symbol,
            accounts=(account,),
            window_start_ns=agg.first_ts,
            window_end_ns=window.close_ns,
            order_ids=tuple(agg.order_ids),
            score=round(score, 4),
            features={
                "aggressive_qty": float(total),
                "aggressive_orders": float(len(agg.order_ids)),
                "volume_share": round(share, 4),
                "one_sided": round(one_sided, 4),
                "impact_ticks": float(impact),
                "direction": float(direction),
            },
            emitted_ts=now,
        )

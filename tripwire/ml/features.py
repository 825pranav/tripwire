"""Per-account windowed behaviour features, computed from the event stream alone.

Windows are tumbling, `window_s` long, aligned to the epoch, and keyed by
(account, symbol). A symbol's windows close when that symbol's event time crosses a
boundary, so results do not depend on how a consumer interleaves symbols.

Features (one row per window with any activity):
- order_to_trade:       log(1 + orders / max(1, fills))
- cancel_ratio:         user cancels / max(orders, cancels)
- median_lifetime_s:    log(1 + median seconds from entry to cancel); window length if none
- size_asymmetry:       log(1 + largest order / the account's median order size)
- price_impact_ticks:   symbol move over the window in the direction of the account's net
                        aggressive flow, scaled by its share of the symbol's volume
- end_of_session_share: the account's aggressive share of volume in the session's last
                        5 minutes (0 outside them)
- self_trade_ratio:     share of its traded volume where the counterparty has the same
                        beneficial owner
- log_orders:           log(1 + orders), an activity level
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

import msgspec

from tripwire.common.events import (
    CancelReason,
    Event,
    OrderAccepted,
    OrderCancelled,
    Side,
    Trade,
)
from tripwire.common.reference import ReferenceData

SECOND = 1_000_000_000
CLOSE_PERIOD_NS = 300 * SECOND

FEATURES = (
    "order_to_trade",
    "cancel_ratio",
    "median_lifetime_s",
    "size_asymmetry",
    "price_impact_ticks",
    "end_of_session_share",
    "self_trade_ratio",
    "log_orders",
)


class WindowRow(msgspec.Struct, frozen=True):
    account: str
    symbol: str
    start_ns: int
    end_ns: int
    features: tuple[float, ...]
    order_ids: tuple[int, ...]  # the largest orders and any same-owner trades


class _Acc:
    __slots__ = (
        "aggr_buy",
        "aggr_sell",
        "buy_sizes",
        "cancels",
        "close_aggr",
        "fills",
        "lifetimes",
        "notable",
        "orders",
        "self_qty",
        "sell_sizes",
        "traded_qty",
    )

    def __init__(self) -> None:
        self.orders = 0
        self.cancels = 0
        self.fills = 0
        self.lifetimes: list[float] = []
        self.buy_sizes: list[int] = []
        self.sell_sizes: list[int] = []
        self.traded_qty = 0
        self.self_qty = 0
        self.aggr_buy = 0
        self.aggr_sell = 0
        self.close_aggr = 0
        self.notable: list[tuple[int, int]] = []  # (qty, order_id)


class _SymbolWindow:
    __slots__ = ("close_volume", "first_price", "last_price", "volume")

    def __init__(self, price: int) -> None:
        self.first_price = price
        self.last_price = price
        self.volume = 0
        self.close_volume = 0


class WindowFeatures:
    """Streaming feature extractor. `process` returns rows for windows that just closed."""

    def __init__(self, reference: ReferenceData, window_s: int = 300) -> None:
        self.owner_of = reference.owner_of()
        self.calendar = reference.session
        self.window_ns = window_s * SECOND
        self.window_end: dict[str, int] = {}
        self.accs: dict[str, dict[str, _Acc]] = {}  # symbol -> account -> accumulator
        self.symbols: dict[str, _SymbolWindow] = {}
        self.last_price: dict[str, int] = {}
        # order_id -> [accepted_ts, remaining qty]
        self.live: dict[int, list[int]] = {}
        self.session_open = 0  # cached session bounds of the current trading day
        self.close_start = 0
        self.close_end = -1

    def process(self, event: Event) -> list[WindowRow]:
        rows: list[WindowRow] = []
        symbol = event.symbol
        if event.ts >= self.window_end.get(symbol, 0):
            if symbol in self.accs:
                rows = self._close(symbol)
            self.window_end[symbol] = (event.ts // self.window_ns + 1) * self.window_ns
        if isinstance(event, OrderAccepted):
            self._on_accept(event)
        elif isinstance(event, Trade):
            self._on_trade(event)
        elif isinstance(event, OrderCancelled):
            self._on_cancel(event)
        return rows

    def finish(self) -> list[WindowRow]:
        rows: list[WindowRow] = []
        for symbol in list(self.accs):
            rows.extend(self._close(symbol))
        return rows

    def run(self, events: Iterable[Event]) -> list[WindowRow]:
        rows: list[WindowRow] = []
        for event in events:
            rows.extend(self.process(event))
        rows.extend(self.finish())
        return rows

    def _acc(self, account: str, symbol: str) -> _Acc:
        accs = self.accs.get(symbol)
        if accs is None:
            accs = self.accs[symbol] = {}
        acc = accs.get(account)
        if acc is None:
            acc = accs[account] = _Acc()
        return acc

    def _on_accept(self, event: OrderAccepted) -> None:
        acc = self._acc(event.account, event.symbol)
        acc.orders += 1
        (acc.buy_sizes if event.side is Side.BUY else acc.sell_sizes).append(event.qty)
        acc.notable.append((event.qty, event.order_id))
        self.live[event.order_id] = [event.ts, event.qty]

    def _on_cancel(self, event: OrderCancelled) -> None:
        entry = self.live.pop(event.order_id, None)
        if event.reason is not CancelReason.USER:
            return
        acc = self._acc(event.account, event.symbol)
        acc.cancels += 1
        if entry is not None:
            acc.lifetimes.append((event.ts - entry[0]) / SECOND)

    def _on_trade(self, trade: Trade) -> None:
        symbol = trade.symbol
        sym = self.symbols.get(symbol)
        if sym is None:
            sym = self.symbols[symbol] = _SymbolWindow(self.last_price.get(symbol, trade.price))
        sym.last_price = trade.price
        sym.volume += trade.qty
        self.last_price[symbol] = trade.price
        if not self.session_open <= trade.ts < self.close_end:
            self.session_open, self.close_end = self.calendar.bounds(trade.ts)
            self.close_start = self.close_end - CLOSE_PERIOD_NS
        in_close = self.close_start <= trade.ts < self.close_end
        if in_close:
            sym.close_volume += trade.qty
        same_owner = self.owner_of.get(trade.buy_account) == self.owner_of.get(trade.sell_account)
        for account, order_id, side in (
            (trade.buy_account, trade.buy_order_id, Side.BUY),
            (trade.sell_account, trade.sell_order_id, Side.SELL),
        ):
            acc = self._acc(account, symbol)
            acc.fills += 1
            acc.traded_qty += trade.qty
            if same_owner:
                acc.self_qty += trade.qty
                acc.notable.append((1 << 40, order_id))  # always keep these ids
            if side is trade.aggressor_side:
                if side is Side.BUY:
                    acc.aggr_buy += trade.qty
                else:
                    acc.aggr_sell += trade.qty
                if in_close:
                    acc.close_aggr += trade.qty
            entry = self.live.get(order_id)
            if entry is not None:
                entry[1] -= trade.qty
                if entry[1] <= 0:
                    del self.live[order_id]

    def _close(self, symbol: str) -> list[WindowRow]:
        end = self.window_end[symbol]
        start = end - self.window_ns
        window_s = self.window_ns / SECOND
        rows = []
        sym = self.symbols.pop(symbol, None)
        for account, acc in self.accs.pop(symbol).items():
            if acc.orders < 2 and acc.fills == 0:
                continue
            impact = 0.0
            eos_share = 0.0
            if sym is not None and sym.volume:
                net = acc.aggr_buy - acc.aggr_sell
                direction = (net > 0) - (net < 0)
                share = (acc.aggr_buy + acc.aggr_sell) / sym.volume
                impact = max(0.0, (sym.last_price - sym.first_price) * direction * share)
                if sym.close_volume:
                    eos_share = acc.close_aggr / sym.close_volume
            lifetime = statistics.median(acc.lifetimes) if acc.lifetimes else window_s
            features = (
                math.log1p(acc.orders / max(1, acc.fills)),
                acc.cancels / max(acc.orders, acc.cancels, 1),
                math.log1p(lifetime),
                _size_outlier(acc.buy_sizes + acc.sell_sizes),
                impact,
                eos_share,
                acc.self_qty / acc.traded_qty if acc.traded_qty else 0.0,
                math.log1p(acc.orders),
            )
            notable = sorted(acc.notable, reverse=True)[:12]
            order_ids = tuple(dict.fromkeys(oid for _, oid in notable))
            rows.append(WindowRow(account, symbol, start, end, features, order_ids))
        return rows


def _size_outlier(sizes: list[int]) -> float:
    if not sizes:
        return 0.0
    return math.log1p(max(sizes) / statistics.median(sizes))

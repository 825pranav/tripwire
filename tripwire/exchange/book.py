"""Limit order book for a single symbol.

Each side is a SortedDict of price levels. Bids are keyed by negative price so that the
first key on either side is always the best price. A level is an insertion-ordered dict
of order_id -> RestingOrder, which gives time priority within the level and O(1) removal
for cancels.
"""

from __future__ import annotations

from itertools import islice
from typing import cast

from sortedcontainers import SortedDict

from tripwire.common.events import Side

Level = dict[int, "RestingOrder"]


class RestingOrder:
    __slots__ = ("account", "order_id", "price", "qty", "side", "ts")

    def __init__(
        self, order_id: int, account: str, side: Side, price: int, qty: int, ts: int
    ) -> None:
        self.order_id = order_id
        self.account = account
        self.side = side
        self.price = price
        self.qty = qty  # remaining (unfilled) quantity
        self.ts = ts


class OrderBook:
    __slots__ = ("asks", "bids", "orders", "symbol")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.bids = SortedDict()  # -price -> Level
        self.asks = SortedDict()  # price -> Level
        self.orders: dict[int, RestingOrder] = {}

    def best_bid(self) -> int | None:
        if not self.bids:
            return None
        return -cast(int, self.bids.peekitem(0)[0])

    def best_ask(self) -> int | None:
        if not self.asks:
            return None
        return cast(int, self.asks.peekitem(0)[0])

    def add(self, order: RestingOrder) -> None:
        if order.side is Side.BUY:
            levels, key = self.bids, -order.price
        else:
            levels, key = self.asks, order.price
        level = cast(Level | None, levels.get(key))
        if level is None:
            level = {}
            levels[key] = level
        level[order.order_id] = order
        self.orders[order.order_id] = order

    def remove(self, order_id: int) -> RestingOrder | None:
        order = self.orders.pop(order_id, None)
        if order is None:
            return None
        if order.side is Side.BUY:
            levels, key = self.bids, -order.price
        else:
            levels, key = self.asks, order.price
        level = cast(Level, levels[key])
        del level[order_id]
        if not level:
            del levels[key]
        return order

    def depth(self, n: int) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
        """Top-n levels per side as (price, total qty), best first."""
        bids = tuple(
            (-cast(int, key), sum(o.qty for o in cast(Level, level).values()))
            for key, level in islice(self.bids.items(), n)
        )
        asks = tuple(
            (cast(int, key), sum(o.qty for o in cast(Level, level).values()))
            for key, level in islice(self.asks.items(), n)
        )
        return bids, asks

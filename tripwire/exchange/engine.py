"""Multi-symbol matching engine with strict price-time priority.

Pure and deterministic: every call takes the event timestamp from the caller and returns
the events it produced, in order. No I/O, no clocks, no logging.

Rules:
- An incoming order matches against the best opposite level first; within a level the
  oldest resting order fills first. Trades print at the resting order's price.
- A limit order's unfilled remainder rests on the book. A market order's remainder is
  cancelled (`CancelReason.UNFILLED`).
- Self-trade prevention: if the incoming order would match a resting order from the same
  account, the resting order is cancelled (`CancelReason.SELF_TRADE`) and matching
  continues. Different accounts of the same beneficial owner are *not* blocked, which is
  exactly the gap wash traders use.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from tripwire.common.events import (
    BookSnapshot,
    CancelReason,
    Event,
    OrderAccepted,
    OrderCancelled,
    OrderType,
    Side,
    Trade,
)
from tripwire.exchange.book import Level, OrderBook, RestingOrder


class MatchingEngine:
    __slots__ = ("_next_order_id", "_next_trade_id", "books")

    def __init__(
        self, symbols: Iterable[str], first_order_id: int = 1, first_trade_id: int = 1
    ) -> None:
        self.books = {symbol: OrderBook(symbol) for symbol in symbols}
        self._next_order_id = first_order_id
        self._next_trade_id = first_trade_id

    def submit_limit(
        self, ts: int, symbol: str, account: str, side: Side, price: int, qty: int
    ) -> tuple[int, list[Event]]:
        if price <= 0:
            raise ValueError(f"limit price must be positive, got {price}")
        return self._submit(ts, symbol, account, side, OrderType.LIMIT, price, qty)

    def submit_market(
        self, ts: int, symbol: str, account: str, side: Side, qty: int
    ) -> tuple[int, list[Event]]:
        return self._submit(ts, symbol, account, side, OrderType.MARKET, None, qty)

    def cancel(self, ts: int, symbol: str, order_id: int) -> list[Event]:
        """Cancel a resting order. Unknown, filled or already-cancelled ids are a no-op."""
        order = self.books[symbol].remove(order_id)
        if order is None:
            return []
        return [_cancelled(ts, symbol, order, CancelReason.USER)]

    def expire_all(self, ts: int) -> list[Event]:
        """End of session: cancel every resting day order, oldest order_id first."""
        events: list[Event] = []
        for symbol, book in self.books.items():
            for order_id in sorted(book.orders):
                order = book.remove(order_id)
                assert order is not None
                events.append(_cancelled(ts, symbol, order, CancelReason.EXPIRED))
        return events

    def snapshot(self, ts: int, symbol: str, depth: int = 10) -> BookSnapshot:
        bids, asks = self.books[symbol].depth(depth)
        return BookSnapshot(ts=ts, symbol=symbol, bids=bids, asks=asks)

    def _submit(
        self,
        ts: int,
        symbol: str,
        account: str,
        side: Side,
        order_type: OrderType,
        price: int | None,
        qty: int,
    ) -> tuple[int, list[Event]]:
        if qty <= 0:
            raise ValueError(f"order quantity must be positive, got {qty}")
        book = self.books[symbol]
        order_id = self._next_order_id
        self._next_order_id += 1
        events: list[Event] = [
            OrderAccepted(
                ts=ts,
                order_id=order_id,
                symbol=symbol,
                account=account,
                side=side,
                order_type=order_type,
                price=price,
                qty=qty,
            )
        ]
        remaining = self._match(ts, book, order_id, account, side, price, qty, events)
        if remaining:
            if price is not None:
                book.add(RestingOrder(order_id, account, side, price, remaining, ts))
            else:
                events.append(
                    OrderCancelled(
                        ts=ts,
                        order_id=order_id,
                        symbol=symbol,
                        account=account,
                        side=side,
                        price=None,
                        qty=remaining,
                        reason=CancelReason.UNFILLED,
                    )
                )
        return order_id, events

    def _match(
        self,
        ts: int,
        book: OrderBook,
        order_id: int,
        account: str,
        side: Side,
        limit: int | None,
        qty: int,
        events: list[Event],
    ) -> int:
        is_buy = side is Side.BUY
        opposite = book.asks if is_buy else book.bids
        remaining = qty
        while remaining and opposite:
            key, level = opposite.peekitem(0)
            level = cast(Level, level)
            level_price = cast(int, key) if is_buy else -cast(int, key)
            if limit is not None and (level_price > limit if is_buy else level_price < limit):
                break
            while remaining and level:
                resting = next(iter(level.values()))
                if resting.account == account:
                    book.remove(resting.order_id)
                    events.append(_cancelled(ts, book.symbol, resting, CancelReason.SELF_TRADE))
                    continue
                fill = remaining if remaining < resting.qty else resting.qty
                events.append(
                    Trade(
                        ts=ts,
                        trade_id=self._next_trade_id,
                        symbol=book.symbol,
                        price=level_price,
                        qty=fill,
                        aggressor_side=side,
                        buy_order_id=order_id if is_buy else resting.order_id,
                        sell_order_id=resting.order_id if is_buy else order_id,
                        buy_account=account if is_buy else resting.account,
                        sell_account=resting.account if is_buy else account,
                    )
                )
                self._next_trade_id += 1
                remaining -= fill
                resting.qty -= fill
                if resting.qty == 0:
                    book.remove(resting.order_id)
        return remaining


def _cancelled(ts: int, symbol: str, order: RestingOrder, reason: CancelReason) -> OrderCancelled:
    return OrderCancelled(
        ts=ts,
        order_id=order.order_id,
        symbol=symbol,
        account=order.account,
        side=order.side,
        price=order.price,
        qty=order.qty,
        reason=reason,
    )

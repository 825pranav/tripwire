"""Market event schemas shared by every component.

Conventions:
- Timestamps (`ts`) are integer nanoseconds since the Unix epoch, in *event time*
  (the simulated exchange clock), never wall-clock time.
- Prices are integer ticks (1 tick = $0.01). Market orders carry `price=None`.
- Quantities are integer shares.

Structs are frozen and tagged so a stream of mixed events round-trips through msgpack
as a single union type (see `tripwire.common.codec`).
"""

from __future__ import annotations

from enum import StrEnum

import msgspec


class Side(StrEnum):
    BUY = "B"
    SELL = "S"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    LIMIT = "L"
    MARKET = "M"


class CancelReason(StrEnum):
    USER = "user"  # cancel request from the account
    SELF_TRADE = "stp"  # resting order removed by self-trade prevention
    UNFILLED = "unfilled"  # unfilled remainder of a market order
    EXPIRED = "expired"  # day order still resting at the close


class OrderAccepted(msgspec.Struct, frozen=True, tag="acc", gc=False):
    ts: int
    order_id: int
    symbol: str
    account: str
    side: Side
    order_type: OrderType
    price: int | None
    qty: int


class OrderCancelled(msgspec.Struct, frozen=True, tag="can", gc=False):
    ts: int
    order_id: int
    symbol: str
    account: str
    side: Side
    price: int | None
    qty: int  # quantity removed from the book
    reason: CancelReason


class Trade(msgspec.Struct, frozen=True, tag="trd", gc=False):
    ts: int
    trade_id: int
    symbol: str
    price: int
    qty: int
    aggressor_side: Side
    buy_order_id: int
    sell_order_id: int
    buy_account: str
    sell_account: str


class BookSnapshot(msgspec.Struct, frozen=True, tag="snap", gc=False):
    ts: int
    symbol: str
    bids: tuple[tuple[int, int], ...]  # (price, total qty), best first
    asks: tuple[tuple[int, int], ...]


Event = OrderAccepted | OrderCancelled | Trade | BookSnapshot

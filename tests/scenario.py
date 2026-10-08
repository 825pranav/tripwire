"""Hand-built event streams for detector tests, produced by the real matching engine."""

from __future__ import annotations

from tripwire.common.alerts import Alert
from tripwire.common.events import Event, Side
from tripwire.common.reference import AccountRecord, ReferenceData, SessionCalendar, SymbolRecord
from tripwire.detectors.config import load_config
from tripwire.detectors.suite import DetectorSuite
from tripwire.exchange.engine import MatchingEngine

MS = 1_000_000
SECOND = 1_000_000_000
MINUTE = 60 * SECOND
SYMBOL = "XYZ"
BUY, SELL = Side.BUY, Side.SELL


class Scenario:
    def __init__(self, owners: dict[str, str] | None = None, session_minutes: int = 120) -> None:
        self.calendar = SessionCalendar("2026-03-02", "14:30", session_minutes)
        self.open_ns = self.calendar.open_ns(0)
        self.close_ns = self.open_ns + session_minutes * MINUTE
        self.t = self.open_ns + 10 * MINUTE
        self.engine = MatchingEngine([SYMBOL])
        self.events: list[Event] = []
        owners = owners or {}
        self.reference = ReferenceData(
            accounts=[AccountRecord(a, o) for a, o in owners.items()],
            symbols=[SymbolRecord(SYMBOL, 0.01, 10_000)],
            session=self.calendar,
        )

    def wait(self, ms: float) -> None:
        self.t += int(ms * MS)

    def at_minutes_before_close(self, minutes: float) -> None:
        self.t = self.close_ns - int(minutes * MINUTE)

    def limit(self, account: str, side: Side, price: int, qty: int) -> int:
        order_id, events = self.engine.submit_limit(self.t, SYMBOL, account, side, price, qty)
        self.events.extend(events)
        return order_id

    def market(self, account: str, side: Side, qty: int) -> int:
        order_id, events = self.engine.submit_market(self.t, SYMBOL, account, side, qty)
        self.events.extend(events)
        return order_id

    def cancel(self, *order_ids: int) -> None:
        for order_id in order_ids:
            self.events.extend(self.engine.cancel(self.t, SYMBOL, order_id))

    def snapshot(self) -> None:
        self.events.append(self.engine.snapshot(self.t, SYMBOL))

    def background_book(self, account: str = "MM1", levels: int = 3, qty: int = 300) -> None:
        """A plain two-sided book: bids 9999 down, asks 10001 up."""
        for i in range(levels):
            self.limit(account, BUY, 9_999 - i, qty)
            self.limit(account, SELL, 10_001 + i, qty)
        self.snapshot()
        self.wait(100)

    def close_session(self) -> None:
        self.t = self.close_ns
        self.events.extend(self.engine.expire_all(self.t))
        self.snapshot()

    def alerts(self) -> list[Alert]:
        return DetectorSuite(self.reference, load_config()).run(self.events)


def of_type(alerts: list[Alert], kind: str) -> list[Alert]:
    return [a for a in alerts if a.type == kind]

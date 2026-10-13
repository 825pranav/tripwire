"""Detector interface. Detectors are pure: state lives in the object, no I/O."""

from __future__ import annotations

from typing import Protocol

from tripwire.common.alerts import Alert
from tripwire.common.events import BookSnapshot, Event, OrderAccepted, OrderCancelled, Trade
from tripwire.detectors.state import MarketState

NO_ALERTS: list[Alert] = []  # shared empty result; callers must not mutate it
MS = 1_000_000
SECOND = 1_000_000_000


ALL_EVENTS: tuple[type, ...] = (OrderAccepted, OrderCancelled, Trade, BookSnapshot)


class Detector(Protocol):
    name: str
    # Event types this detector must see. Detectors driven by event time (deadlines,
    # windows) need every event; the suite skips the others for unlisted types.
    wants: tuple[type, ...]

    def on_event(self, event: Event, state: MarketState) -> list[Alert]:
        """Process one event; `state` is the market *before* this event is applied."""
        ...

    def finish(self, ts: int) -> list[Alert]:
        """End of stream: flush anything still pending."""
        ...

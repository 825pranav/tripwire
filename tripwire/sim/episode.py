"""The ground-truth record a manipulator agent keeps about its own episode."""

from __future__ import annotations

import msgspec


class Episode(msgspec.Struct):
    episode_id: str
    type: str  # spoofing | layering | wash | marking_close
    symbol: str
    owner: str
    accounts: list[str]
    start_ns: int
    end_ns: int
    order_ids: list[int] = msgspec.field(default_factory=list)

    def touch(self, ts: int, order_id: int | None = None) -> None:
        """Extend the episode to `ts` and record an order that belongs to it."""
        self.end_ns = max(self.end_ns, ts)
        if order_id is not None:
            self.order_ids.append(order_id)

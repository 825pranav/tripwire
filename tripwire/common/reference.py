"""Exchange reference data: account -> beneficial-owner registry, symbols and the
trading calendar.

This mirrors the KYC data a real exchange holds, so detectors may legitimately use it
(e.g. to see that two accounts share a beneficial owner). It says nothing about which
accounts manipulate the market.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import msgspec

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
TICK_SIZE = 0.01  # dollars per price tick
SECOND = 1_000_000_000
DAY = 86_400 * SECOND


class SessionCalendar(msgspec.Struct, frozen=True):
    """One session per calendar day starting at `first_date`, opening at `open_time_utc`."""

    first_date: str
    open_time_utc: str
    minutes: int

    def open_ns(self, day: int) -> int:
        hour, minute = (int(part) for part in self.open_time_utc.split(":"))
        d = date.fromisoformat(self.first_date) + timedelta(days=day)
        opened = datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)
        return int(opened.timestamp()) * SECOND

    def day_of(self, ts: int) -> int:
        return (ts - self.open_ns(0)) // DAY

    def bounds(self, ts: int) -> tuple[int, int]:
        """(open, close) of the session that `ts` belongs to."""
        opened = self.open_ns(self.day_of(ts))
        return opened, opened + self.minutes * 60 * SECOND


class AccountRecord(msgspec.Struct, frozen=True):
    account: str
    owner: str


class SymbolRecord(msgspec.Struct, frozen=True):
    symbol: str
    tick_size: float
    reference_price: int  # ticks


class ReferenceData(msgspec.Struct, frozen=True):
    accounts: list[AccountRecord]
    symbols: list[SymbolRecord]
    session: SessionCalendar

    def owner_of(self) -> dict[str, str]:
        return {rec.account: rec.owner for rec in self.accounts}


def write_reference(data: ReferenceData, directory: Path = REFERENCE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference.json"
    path.write_bytes(msgspec.json.format(msgspec.json.encode(data), indent=2) + b"\n")
    return path


def load_reference(directory: Path = REFERENCE_DIR) -> ReferenceData:
    return msgspec.json.decode((directory / "reference.json").read_bytes(), type=ReferenceData)

"""Exchange reference data: account -> beneficial-owner registry and the symbol list.

This mirrors the KYC data a real exchange holds, so detectors may legitimately use it
(e.g. to see that two accounts share a beneficial owner). It says nothing about which
accounts manipulate the market.
"""

from __future__ import annotations

from pathlib import Path

import msgspec

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
TICK_SIZE = 0.01  # dollars per price tick


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

    def owner_of(self) -> dict[str, str]:
        return {rec.account: rec.owner for rec in self.accounts}


def write_reference(data: ReferenceData, directory: Path = REFERENCE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference.json"
    path.write_bytes(msgspec.json.format(msgspec.json.encode(data), indent=2) + b"\n")
    return path


def load_reference(directory: Path = REFERENCE_DIR) -> ReferenceData:
    return msgspec.json.decode((directory / "reference.json").read_bytes(), type=ReferenceData)

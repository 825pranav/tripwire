"""Static regulation references by alert type (from `regulations.toml`)."""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

import msgspec

PATH = Path(__file__).with_name("regulations.toml")
DISCLAIMER = "General reference, not legal advice."


class Regulation(msgspec.Struct, frozen=True):
    jurisdiction: str
    citation: str
    summary: str


@cache
def _load() -> dict[str, list[Regulation]]:
    with PATH.open("rb") as fh:
        return msgspec.convert(tomllib.load(fh), type=dict[str, list[Regulation]])


def regulations_for(alert_type: str) -> list[Regulation]:
    return list(_load().get(alert_type, []))

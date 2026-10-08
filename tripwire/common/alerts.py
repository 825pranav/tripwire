"""Alert schema: what detectors (rules and ML) emit to the `alerts` topic."""

from __future__ import annotations

import msgspec

ALERT_TYPES = ("spoofing", "layering", "wash", "marking_close", "ml_anomaly")


class Alert(msgspec.Struct, frozen=True, kw_only=True):
    alert_id: str
    type: str  # one of ALERT_TYPES
    symbol: str
    accounts: tuple[str, ...]
    window_start_ns: int  # event-time window the alert covers
    window_end_ns: int
    order_ids: tuple[int, ...]  # orders that triggered the alert
    score: float  # 0..1, higher = more suspicious
    features: dict[str, float]
    emitted_ts: int  # event time of the event that fired the alert
    source: str = "rules"  # rules | ml | fusion


def make_alert_id(kind: str, symbol: str, account: str, ts: int) -> str:
    return f"{kind}-{symbol}-{account}-{ts}"


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value

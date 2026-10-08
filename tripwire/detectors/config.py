"""Typed detector thresholds, loaded from `detectors/config.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

import msgspec

CONFIG_PATH = Path(__file__).with_name("config.toml")


class SpoofingConfig(msgspec.Struct, frozen=True):
    cancel_window_ms: int = 100
    min_large_qty: int = 2000
    touch_multiple: float = 3.0
    fill_ratio: float = 5.0
    min_side_asymmetry: float = 6.0
    max_ticks_from_touch: int = 2
    max_lifetime_s: int = 60


class LayeringConfig(msgspec.Struct, frozen=True):
    cancel_window_ms: int = 100
    min_levels: int = 3
    min_total_qty: int = 2500
    fill_ratio: float = 5.0
    max_ticks_from_touch: int = 6


class WashConfig(msgspec.Struct, frozen=True):
    window_s: int = 600
    min_trades: int = 3
    max_resting_ms: int = 2000


class MarkingCloseConfig(msgspec.Struct, frozen=True):
    window_s: int = 300
    min_aggressive_qty: int = 3000
    min_share: float = 0.25
    min_one_sided: float = 0.85
    min_impact_ticks: int = 4
    min_orders: int = 5


class DetectorConfig(msgspec.Struct, frozen=True):
    spoofing: SpoofingConfig = SpoofingConfig()
    layering: LayeringConfig = LayeringConfig()
    wash: WashConfig = WashConfig()
    marking_close: MarkingCloseConfig = MarkingCloseConfig()


def load_config(path: Path = CONFIG_PATH) -> DetectorConfig:
    with path.open("rb") as fh:
        return msgspec.convert(tomllib.load(fh), type=DetectorConfig)

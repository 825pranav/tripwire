"""Simulator configuration. Defaults describe one compressed trading session."""

from __future__ import annotations

import msgspec


class SymbolSpec(msgspec.Struct, frozen=True):
    symbol: str
    start_price: int  # ticks
    volatility: float  # std dev of the fair-value random walk, ticks per second


DEFAULT_SYMBOLS = (
    SymbolSpec("ACME", 10_000, 0.8),
    SymbolSpec("BOLT", 2_500, 0.5),
    SymbolSpec("CRUX", 4_200, 0.6),
)


class EpisodeCounts(msgspec.Struct, frozen=True):
    """Manipulation episodes injected per simulated day."""

    spoofing: int = 3
    layering: int = 2
    wash: int = 2
    marking_close: int = 1


CLEAN_DAY = EpisodeCounts(spoofing=0, layering=0, wash=0, marking_close=0)


class SimConfig(msgspec.Struct, frozen=True):
    symbols: tuple[SymbolSpec, ...] = DEFAULT_SYMBOLS
    session_minutes: int = 120
    first_session_date: str = "2026-03-02"  # day 0; day k is k calendar days later
    open_time_utc: str = "14:30"
    snapshot_interval_ms: int = 500
    snapshot_depth: int = 10

    # Population (fixed across days, assigned by population_seed, not the day seed)
    population_seed: int = 0
    market_makers: int = 2
    noise_traders: int = 20
    momentum_traders: int = 3
    institutional_accounts: int = 2  # one owner, several accounts
    manipulator_accounts: int = 4  # single-account manipulators
    wash_owners: int = 2  # each owns two accounts

    # Market makers
    mm_requote_mean_ms: float = 1_500.0
    mm_half_spread_ticks: tuple[int, ...] = (1, 1, 2)
    mm_lots: tuple[int, ...] = (2, 3, 4, 5)  # x100 shares
    mm_fill_reaction_ms: tuple[int, int] = (1, 5)

    # Noise traders
    noise_mean_interarrival_s: float = 15.0
    noise_market_order_prob: float = 0.4
    noise_imbalance_sensitivity: float = 0.35
    noise_limit_lifetime_mean_s: float = 20.0

    # Momentum traders
    momentum_check_mean_s: float = 4.0
    momentum_threshold_ticks: int = 6

    # Background activity for manipulator accounts outside their episodes
    manipulator_background_interarrival_s: float = 90.0

    episodes: EpisodeCounts = EpisodeCounts()

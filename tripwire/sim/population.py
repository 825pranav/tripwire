"""The trading population: which account plays which role, and who owns it.

Account and owner ids are shuffled with `population_seed` so neither reveals a role.
The population stays fixed across simulated days, like a real member base.
"""

from __future__ import annotations

import random

import msgspec

from tripwire.common.reference import (
    TICK_SIZE,
    AccountRecord,
    ReferenceData,
    SessionCalendar,
    SymbolRecord,
)
from tripwire.sim.config import SimConfig


class Population(msgspec.Struct, frozen=True):
    owner_of: dict[str, str]
    market_makers: list[str]
    noise_traders: list[str]
    momentum_traders: list[str]
    institutional: list[str]
    manipulators: list[str]
    wash_pairs: list[tuple[str, str]]


def build_population(config: SimConfig) -> Population:
    rng = random.Random(f"population:{config.population_seed}")
    singles = (
        config.market_makers
        + config.noise_traders
        + config.momentum_traders
        + config.manipulator_accounts
    )
    n_accounts = singles + config.institutional_accounts + 2 * config.wash_owners
    n_owners = singles + 1 + config.wash_owners

    accounts = [f"ACC{i:03d}" for i in range(1, n_accounts + 1)]
    owners = [f"OWN{i:03d}" for i in range(1, n_owners + 1)]
    rng.shuffle(accounts)
    rng.shuffle(owners)
    acc_iter, own_iter = iter(accounts), iter(owners)

    owner_of: dict[str, str] = {}

    def take_singles(n: int) -> list[str]:
        out = [next(acc_iter) for _ in range(n)]
        for acc in out:
            owner_of[acc] = next(own_iter)
        return out

    market_makers = take_singles(config.market_makers)
    noise = take_singles(config.noise_traders)
    momentum = take_singles(config.momentum_traders)
    manipulators = take_singles(config.manipulator_accounts)

    institutional = [next(acc_iter) for _ in range(config.institutional_accounts)]
    inst_owner = next(own_iter)
    for acc in institutional:
        owner_of[acc] = inst_owner

    wash_pairs: list[tuple[str, str]] = []
    for _ in range(config.wash_owners):
        pair = (next(acc_iter), next(acc_iter))
        owner = next(own_iter)
        owner_of[pair[0]] = owner
        owner_of[pair[1]] = owner
        wash_pairs.append(pair)

    return Population(
        owner_of=dict(sorted(owner_of.items())),
        market_makers=market_makers,
        noise_traders=noise,
        momentum_traders=momentum,
        institutional=institutional,
        manipulators=manipulators,
        wash_pairs=wash_pairs,
    )


def reference_data(config: SimConfig, population: Population) -> ReferenceData:
    return ReferenceData(
        accounts=[AccountRecord(acc, owner) for acc, owner in population.owner_of.items()],
        symbols=[SymbolRecord(s.symbol, TICK_SIZE, s.start_price) for s in config.symbols],
        session=calendar(config),
    )


def calendar(config: SimConfig) -> SessionCalendar:
    return SessionCalendar(config.first_session_date, config.open_time_utc, config.session_minutes)

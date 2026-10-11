"""Assemble and run one simulated trading day."""

from __future__ import annotations

import msgspec

from tripwire.sim.agents import Institutional, MarketMaker, MomentumTrader, NoiseTrader
from tripwire.sim.config import SimConfig
from tripwire.sim.core import Simulation, Sink
from tripwire.sim.episode import Episode
from tripwire.sim.manipulators import schedule_episodes
from tripwire.sim.population import Population, build_population


class DayResult(msgspec.Struct):
    seed: int
    day: int
    open_ns: int
    close_ns: int
    event_count: int
    episodes: list[Episode]


def build_simulation(
    config: SimConfig, population: Population, seed: int, day: int, sink: Sink
) -> Simulation:
    sim = Simulation(config, population, seed, day, sink)
    for account in population.market_makers:
        sim.agents.append(MarketMaker(account, sim.rng_for(f"mm:{account}"), config))
    for account in population.noise_traders:
        rng = sim.rng_for(f"noise:{account}")
        sim.agents.append(NoiseTrader(account, rng, config, config.noise_mean_interarrival_s))
    for account in population.momentum_traders:
        sim.agents.append(MomentumTrader(account, sim.rng_for(f"momentum:{account}"), config))
    for account in population.institutional:
        rng = sim.rng_for(f"inst:{account}")
        sim.agents.append(Institutional(account, rng, rebalance_today=rng.random() < 0.5))
    # Manipulator accounts also trade normally outside their episodes, so an account's
    # identity alone never gives it away.
    background = population.manipulators + [a for pair in population.wash_pairs for a in pair]
    for account in background:
        rng = sim.rng_for(f"background:{account}")
        interarrival = config.manipulator_background_interarrival_s
        sim.agents.append(NoiseTrader(account, rng, config, interarrival))
    sim.agents.extend(schedule_episodes(sim, config.episodes))
    return sim


def simulate_day(
    config: SimConfig,
    seed: int,
    day: int,
    sink: Sink,
    speed: float | None = None,
    population: Population | None = None,
) -> DayResult:
    population = population or build_population(config)
    sim = build_simulation(config, population, seed, day, sink)
    sim.run(speed)
    episodes = [ep for ep in sim.episodes if ep.order_ids]
    return DayResult(seed, day, sim.open_ns, sim.close_ns, sim.event_count, episodes)

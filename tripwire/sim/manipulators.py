"""Manipulator agents, one per injected episode. Each records its own ground-truth
`Episode` (type, owner, accounts, time window, order_ids) for the eval labels."""

from __future__ import annotations

import random
from functools import partial

from tripwire.common.events import Side, Trade
from tripwire.sim.config import EpisodeCounts
from tripwire.sim.core import MINUTE, MS, SECOND, Agent, Simulation
from tripwire.sim.episode import Episode


def _best(sim: Simulation, symbol: str, side: Side) -> int | None:
    return sim.best_bid(symbol) if side is Side.BUY else sim.best_ask(symbol)


class Spoofer(Agent):
    """Spoofing (`layers=1`) or layering (`layers>1`).

    Each cycle: rest a large fake order (or a ladder of orders at stepped prices) near the
    touch on side A, then post a small genuine order on side B. Once the genuine order
    gets a fill, cancel everything on side A within milliseconds. A cycle with no fill
    gives up after 15-45s.
    """

    def __init__(
        self, account: str, rng: random.Random, symbol: str, start_ns: int, cycles: int, layers: int
    ) -> None:
        super().__init__(account, rng)
        self.symbol = symbol
        self.start_ns = start_ns
        self.cycles_left = cycles
        self.layers = layers
        self.episode: Episode | None = None
        self.fake_ids: list[int] = []
        self.genuine_id: int | None = None
        self.cycle_no = 0
        self.closing = False

    def start(self, sim: Simulation) -> None:
        sim.at(self.start_ns, partial(self._cycle, sim))

    def _cycle(self, sim: Simulation) -> None:
        rng, symbol = self.rng, self.symbol
        fake_side = rng.choice((Side.BUY, Side.SELL))
        touch = _best(sim, symbol, fake_side)
        if touch is None or _best(sim, symbol, fake_side.opposite) is None:
            sim.after(SECOND, partial(self._cycle, sim))
            return
        if self.episode is None:
            kind = "layering" if self.layers > 1 else "spoofing"
            self.episode = sim.new_episode(kind, symbol, [self.account])
        self.cycle_no += 1
        self.closing = False
        self.genuine_id = None
        self.fake_ids = []
        step = -1 if fake_side is Side.BUY else 1
        if self.layers > 1:
            levels = [(touch + i * step, rng.randint(4, 12) * 100) for i in range(self.layers)]
        else:
            levels = [(touch + rng.choice((0, 0, 1, 2)) * step, rng.randint(12, 60) * 100)]
        for price, qty in levels:
            order_id = sim.submit_limit(self, symbol, fake_side, price, qty)
            self.fake_ids.append(order_id)
            self.episode.touch(sim.now, order_id)
        delay = rng.randint(2, 15) * MS
        sim.after(delay, partial(self._place_genuine, sim, fake_side.opposite, self.cycle_no))

    def _place_genuine(self, sim: Simulation, side: Side, cycle_no: int) -> None:
        assert self.episode is not None
        bid, ask = sim.best_bid(self.symbol), sim.best_ask(self.symbol)
        if bid is None or ask is None:
            self._finish(sim, cycle_no)
            return
        improve = 1 if ask - bid >= 2 else 0  # step inside the spread when there is room
        price = bid + improve if side is Side.BUY else ask - improve
        qty = self.rng.randint(1, 3) * 100
        self.genuine_id = sim.submit_limit(self, self.symbol, side, price, qty)
        self.episode.touch(sim.now, self.genuine_id)
        if self.genuine_id not in self.live:  # filled on arrival
            self._react_to_fill(sim)
        else:
            timeout = self.rng.randint(15, 45) * SECOND
            sim.after(timeout, partial(self._finish, sim, cycle_no))

    def on_fill(self, sim: Simulation, order_id: int, trade: Trade) -> None:
        super().on_fill(sim, order_id, trade)
        if order_id == self.genuine_id:
            self._react_to_fill(sim)

    def _react_to_fill(self, sim: Simulation) -> None:
        if not self.closing:
            self.closing = True
            # Most spoofers pull within tens of milliseconds; some are slower.
            fast = self.rng.random() < 0.75
            delay = (self.rng.randint(1, 40) if fast else self.rng.randint(60, 600)) * MS
            sim.after(delay, partial(self._finish, sim, self.cycle_no))

    def _finish(self, sim: Simulation, cycle_no: int) -> None:
        if cycle_no != self.cycle_no or self.episode is None:
            return
        self.cycle_no += 1  # invalidate any other pending finish for this cycle
        ids = [*self.fake_ids, self.genuine_id] if self.genuine_id else self.fake_ids
        for order_id in ids:
            if order_id in self.live:
                sim.cancel(self.symbol, order_id)
        self.episode.touch(sim.now)
        self.cycles_left -= 1
        if self.cycles_left > 0:
            sim.after(self.rng.randint(3, 20) * SECOND, partial(self._cycle, sim))


class WashTrader(Agent):
    """Two accounts with the same beneficial owner trade with each other: one posts
    inside the spread, the other crosses it a few milliseconds later."""

    def __init__(
        self,
        accounts: tuple[str, str],
        rng: random.Random,
        symbol: str,
        start_ns: int,
        trades: int,
    ) -> None:
        super().__init__(accounts[0], rng)
        self.accounts = accounts
        self.symbol = symbol
        self.start_ns = start_ns
        self.trades_left = trades
        self.episode: Episode | None = None

    def start(self, sim: Simulation) -> None:
        sim.at(self.start_ns, partial(self._trade, sim))

    def _trade(self, sim: Simulation) -> None:
        rng = self.rng
        bid, ask = sim.best_bid(self.symbol), sim.best_ask(self.symbol)
        if bid is None or ask is None or ask - bid < 2:
            sim.after(rng.randint(1, 3) * SECOND, partial(self._trade, sim))
            return
        if self.episode is None:
            self.episode = sim.new_episode("wash", self.symbol, list(self.accounts))
        price = (bid + ask) // 2
        qty = rng.randint(2, 8) * 100
        seller, buyer = self.accounts if rng.random() < 0.5 else self.accounts[::-1]
        sell_id = sim.submit_limit(self, self.symbol, Side.SELL, price, qty, account=seller)
        self.episode.touch(sim.now, sell_id)
        # Usually the second leg follows within milliseconds; sometimes it waits seconds.
        delay = (rng.randint(1, 20) if rng.random() < 0.75 else rng.randint(300, 4000)) * MS
        sim.after(delay, partial(self._cross, sim, buyer, price, qty, sell_id))

    def _cross(self, sim: Simulation, buyer: str, price: int, qty: int, sell_id: int) -> None:
        assert self.episode is not None
        buy_id = sim.submit_limit(self, self.symbol, Side.BUY, price, qty, account=buyer)
        self.episode.touch(sim.now, buy_id)
        sim.after(2 * SECOND, partial(self._cleanup, sim, (sell_id, buy_id)))
        self.trades_left -= 1
        if self.trades_left > 0:
            gap = self.rng.expovariate(1 / 8) * SECOND
            sim.after(int(gap) + 500 * MS, partial(self._trade, sim))

    def _cleanup(self, sim: Simulation, order_ids: tuple[int, int]) -> None:
        assert self.episode is not None
        for order_id in order_ids:
            if order_id in self.live:
                sim.cancel(self.symbol, order_id)
                self.episode.touch(sim.now)


class CloseMarker(Agent):
    """Marking the close: aggressive one-directional orders through the touch in the
    final minutes of the session, accelerating into the last minute."""

    def __init__(self, account: str, rng: random.Random, symbol: str, start_ns: int) -> None:
        super().__init__(account, rng)
        self.symbol = symbol
        self.start_ns = start_ns
        self.side = rng.choice((Side.BUY, Side.SELL))
        self.episode: Episode | None = None

    def start(self, sim: Simulation) -> None:
        sim.at(self.start_ns, partial(self._act, sim))

    def _act(self, sim: Simulation) -> None:
        rng = self.rng
        if self.episode is None:
            self.episode = sim.new_episode("marking_close", self.symbol, [self.account])
        touch = _best(sim, self.symbol, self.side.opposite)
        if touch is None:
            touch = round(sim.mid(self.symbol))
        through = rng.choice((0, 1, 1, 2, 3))
        price = touch + through if self.side is Side.BUY else touch - through
        qty = rng.randint(2, 8) * 100
        order_id = sim.submit_limit(self, self.symbol, self.side, price, qty)
        self.episode.touch(sim.now, order_id)
        remaining = sim.close_ns - sim.now
        gap = rng.randint(3, 10) * SECOND if remaining > MINUTE else rng.randint(1, 3) * SECOND
        if gap < remaining:
            sim.after(gap, partial(self._act, sim))


def schedule_episodes(sim: Simulation, counts: EpisodeCounts) -> list[Agent]:
    """Create the manipulator agents for one day. Mid-session episodes start after the
    opening minutes and well before the close (the last 30 minutes, or the last quarter of
    a short session), so they never overlap the marking-the-close window."""
    rng = sim.rng_for("episodes")
    population = sim.population
    singles = list(population.manipulators)
    rng.shuffle(singles)
    next_single = 0

    def take_single() -> str:
        nonlocal next_single
        account = singles[next_single % len(singles)]
        next_single += 1
        return account

    session = sim.close_ns - sim.open_ns
    earliest = sim.open_ns + min(5 * MINUTE, session // 8)
    latest = sim.close_ns - min(30 * MINUTE, session // 4)

    def mid_session_start() -> int:
        return rng.randint(earliest, latest)

    agents: list[Agent] = []
    for i in range(counts.spoofing):
        rng_i = sim.rng_for(f"spoofing:{i}")
        agents.append(
            Spoofer(
                take_single(),
                rng_i,
                rng.choice(sim.symbols),
                mid_session_start(),
                cycles=rng.randint(3, 6),
                layers=1,
            )
        )
    for i in range(counts.layering):
        rng_i = sim.rng_for(f"layering:{i}")
        agents.append(
            Spoofer(
                take_single(),
                rng_i,
                rng.choice(sim.symbols),
                mid_session_start(),
                cycles=rng.randint(2, 4),
                layers=rng.randint(3, 6),
            )
        )
    for i in range(counts.wash):
        pair = population.wash_pairs[i % len(population.wash_pairs)]
        agents.append(
            WashTrader(
                pair,
                sim.rng_for(f"wash:{i}"),
                rng.choice(sim.symbols),
                mid_session_start(),
                trades=rng.randint(5, 10),
            )
        )
    close_symbols = rng.sample(sim.symbols, min(counts.marking_close, len(sim.symbols)))
    for i, symbol in enumerate(close_symbols):
        start = sim.close_ns - rng.randint(60, 300) * SECOND
        agents.append(CloseMarker(take_single(), sim.rng_for(f"close:{i}"), symbol, start))
    return agents

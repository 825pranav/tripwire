"""Legitimate market participants.

These generate the background the detectors must NOT flag, including two deliberate
hard negatives:
- market makers cancel and replace almost every quote, and requote within milliseconds
  of a fill (the same shape as spoofing, but two-sided and with normal sizes);
- institutional accounts rest large passive orders for minutes, and some days work a
  rebalance order through the last minutes of the session (high end-of-day volume share).
"""

from __future__ import annotations

import random
from functools import partial

from tripwire.common.events import Side, Trade
from tripwire.sim.config import SimConfig
from tripwire.sim.core import MINUTE, MS, SECOND, Agent, Simulation


def _touch(sim: Simulation, symbol: str, side: Side) -> int:
    """Best price on `side`, falling back to the fair value when that side is empty."""
    price = sim.best_bid(symbol) if side is Side.BUY else sim.best_ask(symbol)
    return price if price is not None else round(sim.fair[symbol])


def _passive_price(sim: Simulation, symbol: str, side: Side, ticks_behind: int) -> int:
    touch = _touch(sim, symbol, side)
    return touch - ticks_behind if side is Side.BUY else touch + ticks_behind


class MarketMaker(Agent):
    """Quotes two levels on both sides of every symbol around a blend of fair value and
    mid, skewed against inventory. Cancels and replaces everything on each requote."""

    def __init__(self, account: str, rng: random.Random, config: SimConfig) -> None:
        super().__init__(account, rng)
        self.config = config
        self.quotes: dict[str, list[int]] = {}
        self.inventory: dict[str, int] = {}
        self.fill_requote_pending: dict[str, bool] = {}

    def start(self, sim: Simulation) -> None:
        for symbol in sim.symbols:
            self.quotes[symbol] = []
            self.inventory[symbol] = 0
            self.fill_requote_pending[symbol] = False
            sim.after(self.rng.randint(0, 500) * MS, partial(self._timer, sim, symbol))

    def _timer(self, sim: Simulation, symbol: str) -> None:
        self.requote(sim, symbol)
        delay_ms = self.rng.expovariate(1 / self.config.mm_requote_mean_ms)
        sim.after(int(delay_ms * MS) + MS, partial(self._timer, sim, symbol))

    def requote(self, sim: Simulation, symbol: str) -> None:
        for order_id in self.quotes[symbol]:
            if order_id in self.live:
                sim.cancel(symbol, order_id)
        rng = self.rng
        center = (sim.fair[symbol] + sim.mid(symbol)) / 2 - self.inventory[symbol] / 1_000
        half = rng.choice(self.config.mm_half_spread_ticks)
        bid, ask = round(center) - half, round(center) + half
        best_bid, best_ask = sim.best_bid(symbol), sim.best_ask(symbol)
        if best_ask is not None:
            bid = min(bid, best_ask - 1)  # post-only: never cross the book
        if best_bid is not None:
            ask = max(ask, best_bid + 1)
        lots = self.config.mm_lots
        self.quotes[symbol] = [
            sim.submit_limit(self, symbol, Side.BUY, bid, rng.choice(lots) * 100),
            sim.submit_limit(self, symbol, Side.SELL, ask, rng.choice(lots) * 100),
            sim.submit_limit(self, symbol, Side.BUY, bid - 1, 2 * rng.choice(lots) * 100),
            sim.submit_limit(self, symbol, Side.SELL, ask + 1, 2 * rng.choice(lots) * 100),
        ]

    def on_fill(self, sim: Simulation, order_id: int, trade: Trade) -> None:
        super().on_fill(sim, order_id, trade)
        bought = trade.buy_order_id == order_id
        self.inventory[trade.symbol] += trade.qty if bought else -trade.qty
        if not self.fill_requote_pending[trade.symbol]:
            self.fill_requote_pending[trade.symbol] = True
            low, high = self.config.mm_fill_reaction_ms
            sim.after(
                self.rng.randint(low, high) * MS, partial(self._fill_requote, sim, trade.symbol)
            )

    def _fill_requote(self, sim: Simulation, symbol: str) -> None:
        self.fill_requote_pending[symbol] = False
        self.requote(sim, symbol)


class NoiseTrader(Agent):
    """Poisson order flow: small market orders and short-lived passive limits. Leans
    towards the heavier side of the visible book, which is what makes spoofing pay."""

    def __init__(
        self, account: str, rng: random.Random, config: SimConfig, mean_interarrival_s: float
    ) -> None:
        super().__init__(account, rng)
        self.config = config
        self.mean_interarrival_s = mean_interarrival_s

    def start(self, sim: Simulation) -> None:
        self._schedule_next(sim)

    def _schedule_next(self, sim: Simulation) -> None:
        rate = sim.activity_multiplier() / self.mean_interarrival_s
        sim.after(int(self.rng.expovariate(rate) * SECOND) + MS, partial(self._act, sim))

    def _act(self, sim: Simulation) -> None:
        rng, config = self.rng, self.config
        symbol = rng.choice(sim.symbols)
        lean = config.noise_imbalance_sensitivity * sim.imbalance(symbol)
        p_buy = min(0.9, max(0.1, 0.5 + lean))
        side = Side.BUY if rng.random() < p_buy else Side.SELL
        qty = rng.choice((1, 1, 2, 2, 3, 5)) * 100
        if rng.random() < config.noise_market_order_prob:
            sim.submit_market(self, symbol, side, qty)
        else:
            price = _passive_price(sim, symbol, side, rng.choice((0, 0, 1, 2, 3)))
            order_id = sim.submit_limit(self, symbol, side, price, qty)
            if order_id in self.live:
                lifetime = rng.expovariate(1 / config.noise_limit_lifetime_mean_s)
                sim.after(int(lifetime * SECOND), partial(self._expire, sim, symbol, order_id))
        self._schedule_next(sim)

    def _expire(self, sim: Simulation, symbol: str, order_id: int) -> None:
        if order_id in self.live:
            sim.cancel(symbol, order_id)


class MomentumTrader(Agent):
    """Periodically looks at one symbol; if the mid moved more than a threshold since its
    last look, it chases the move with a market order."""

    def __init__(self, account: str, rng: random.Random, config: SimConfig) -> None:
        super().__init__(account, rng)
        self.config = config
        self.last_seen: dict[str, float] = {}

    def start(self, sim: Simulation) -> None:
        self._schedule_next(sim)

    def _schedule_next(self, sim: Simulation) -> None:
        delay = self.rng.expovariate(1 / self.config.momentum_check_mean_s)
        sim.after(int(delay * SECOND) + MS, partial(self._act, sim))

    def _act(self, sim: Simulation) -> None:
        symbol = self.rng.choice(sim.symbols)
        mid = sim.mid(symbol)
        previous = self.last_seen.get(symbol)
        self.last_seen[symbol] = mid
        if previous is not None and abs(mid - previous) >= self.config.momentum_threshold_ticks:
            side = Side.BUY if mid > previous else Side.SELL
            sim.submit_market(self, symbol, side, self.rng.choice((2, 3, 4)) * 100)
        self._schedule_next(sim)


class Institutional(Agent):
    """Rests large passive orders a few ticks behind the touch for minutes at a time.
    On some days it also works a rebalance order through the last ~20 minutes."""

    def __init__(self, account: str, rng: random.Random, rebalance_today: bool) -> None:
        super().__init__(account, rng)
        self.rebalance_today = rebalance_today

    def start(self, sim: Simulation) -> None:
        sim.after(int(self.rng.uniform(1, 8) * MINUTE), partial(self._post_large, sim))
        if self.rebalance_today:
            begin = sim.close_ns - self.rng.randint(15, 25) * MINUTE
            sim.at(begin, partial(self._begin_rebalance, sim))

    def _post_large(self, sim: Simulation) -> None:
        rng = self.rng
        symbol = rng.choice(sim.symbols)
        side = rng.choice((Side.BUY, Side.SELL))
        price = _passive_price(sim, symbol, side, rng.randint(1, 4))
        order_id = sim.submit_limit(self, symbol, side, price, rng.randint(20, 60) * 100)
        lifetime = rng.uniform(2, 10) * MINUTE
        sim.after(int(lifetime), partial(self._cancel_if_live, sim, symbol, order_id))
        sim.after(int(rng.expovariate(1 / 6) * MINUTE) + SECOND, partial(self._post_large, sim))

    def _cancel_if_live(self, sim: Simulation, symbol: str, order_id: int) -> None:
        if order_id in self.live:
            sim.cancel(symbol, order_id)

    def _begin_rebalance(self, sim: Simulation) -> None:
        rng = self.rng
        symbol = rng.choice(sim.symbols)
        side = rng.choice((Side.BUY, Side.SELL))
        slices = rng.randint(20, 40)
        child_qty = max(100, rng.randint(30, 80) * 100 // slices // 100 * 100)
        interval = (sim.close_ns - sim.now - MINUTE) // slices
        for i in range(slices):
            sim.after(i * interval + 1, partial(self._child, sim, symbol, side, child_qty))

    def _child(self, sim: Simulation, symbol: str, side: Side, qty: int) -> None:
        # Marketable limit at the opposite touch: takes what is there, rests the rest.
        price = _touch(sim, symbol, side.opposite)
        order_id = sim.submit_limit(self, symbol, side, price, qty)
        sim.after(20 * SECOND, partial(self._cancel_if_live, sim, symbol, order_id))

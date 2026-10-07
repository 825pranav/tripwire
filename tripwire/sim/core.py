"""Discrete-event simulation kernel: simulated clock, scheduler, order routing.

Time only advances by popping the next scheduled callback, so a run is a pure function of
(config, population, seed, day). Ties at the same timestamp are broken by scheduling
order. In realtime mode the loop additionally sleeps so simulated time tracks wall time
(scaled by `speed`); the event stream is identical either way.
"""

from __future__ import annotations

import heapq
import math
import random
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial

from tripwire.common.events import Event, OrderCancelled, Side, Trade
from tripwire.exchange.engine import MatchingEngine
from tripwire.sim.config import SimConfig
from tripwire.sim.labels import Episode
from tripwire.sim.population import Population

MS = 1_000_000
SECOND = 1_000_000_000
MINUTE = 60 * SECOND
ID_STRIDE = 1_000_000_000  # order/trade ids for day d start at d * ID_STRIDE + 1

Sink = Callable[[Event], None]


class Agent:
    """Base trading agent.

    Agents act only from scheduled callbacks. `on_fill` / `on_cancelled` may update state
    and schedule a reaction, but must not submit orders directly, so the event stream
    stays in causal order.
    """

    def __init__(self, account: str, rng: random.Random) -> None:
        self.account = account
        self.rng = rng
        self.live: dict[int, int] = {}  # order_id -> remaining qty

    def start(self, sim: Simulation) -> None:
        pass

    def on_fill(self, sim: Simulation, order_id: int, trade: Trade) -> None:
        left = self.live.get(order_id)
        if left is None:
            return
        left -= trade.qty
        if left > 0:
            self.live[order_id] = left
        else:
            del self.live[order_id]

    def on_cancelled(self, sim: Simulation, event: OrderCancelled) -> None:
        self.live.pop(event.order_id, None)


def session_open_ns(config: SimConfig, day: int) -> int:
    hour, minute = (int(part) for part in config.open_time_utc.split(":"))
    first = date.fromisoformat(config.first_session_date) + timedelta(days=day)
    opened = datetime(first.year, first.month, first.day, hour, minute, tzinfo=UTC)
    return int(opened.timestamp()) * SECOND


class Simulation:
    def __init__(
        self, config: SimConfig, population: Population, seed: int, day: int, sink: Sink
    ) -> None:
        self.config = config
        self.population = population
        self.seed = seed
        self.day = day
        self.sink = sink
        self.open_ns = session_open_ns(config, day)
        self.close_ns = self.open_ns + config.session_minutes * MINUTE
        self.now = self.open_ns
        self.symbols = [spec.symbol for spec in config.symbols]
        self.engine = MatchingEngine(
            self.symbols, first_order_id=day * ID_STRIDE + 1, first_trade_id=day * ID_STRIDE + 1
        )
        rng = self.rng_for("fair_value")
        self.fair: dict[str, float] = {
            spec.symbol: spec.start_price * (1 + rng.gauss(0, 0.01)) for spec in config.symbols
        }
        self.last_price: dict[str, int] = {sym: round(px) for sym, px in self.fair.items()}
        self._fair_rng = {sym: self.rng_for(f"fair:{sym}") for sym in self.symbols}
        self._volatility = {spec.symbol: spec.volatility for spec in config.symbols}
        self.agents: list[Agent] = []
        self.episodes: list[Episode] = []
        self.event_count = 0
        self._queue: list[tuple[int, int, Callable[[], None]]] = []
        self._seq = 0
        self._owners: dict[int, Agent] = {}

    # -- scheduling -----------------------------------------------------------------

    def rng_for(self, name: str) -> random.Random:
        return random.Random(f"{self.seed}:{self.day}:{name}")

    def at(self, ts: int, fn: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (ts, self._seq, fn))
        self._seq += 1

    def after(self, delay_ns: int, fn: Callable[[], None]) -> None:
        self.at(self.now + max(delay_ns, 1), fn)

    # -- market view ----------------------------------------------------------------

    def best_bid(self, symbol: str) -> int | None:
        return self.engine.books[symbol].best_bid()

    def best_ask(self, symbol: str) -> int | None:
        return self.engine.books[symbol].best_ask()

    def mid(self, symbol: str) -> float:
        book = self.engine.books[symbol]
        bid, ask = book.best_bid(), book.best_ask()
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return float(self.last_price[symbol])

    def imbalance(self, symbol: str, levels: int = 3) -> float:
        """(bid qty - ask qty) / total over the top levels; in [-1, 1]."""
        bids, asks = self.engine.books[symbol].depth(levels)
        bid_qty = sum(qty for _, qty in bids)
        ask_qty = sum(qty for _, qty in asks)
        total = bid_qty + ask_qty
        return (bid_qty - ask_qty) / total if total else 0.0

    def activity_multiplier(self) -> float:
        """U-shaped intraday activity: busier near the open and the close."""
        since_open = (self.now - self.open_ns) / MINUTE
        to_close = (self.close_ns - self.now) / MINUTE
        return 1.0 + 1.2 * math.exp(-since_open / 10) + 1.2 * math.exp(-to_close / 10)

    # -- order entry ----------------------------------------------------------------

    def submit_limit(
        self,
        agent: Agent,
        symbol: str,
        side: Side,
        price: int,
        qty: int,
        account: str | None = None,
    ) -> int:
        order_id, events = self.engine.submit_limit(
            self.now, symbol, account or agent.account, side, price, qty
        )
        self._register(agent, order_id, qty)
        self._publish(events)
        return order_id

    def submit_market(
        self, agent: Agent, symbol: str, side: Side, qty: int, account: str | None = None
    ) -> int:
        order_id, events = self.engine.submit_market(
            self.now, symbol, account or agent.account, side, qty
        )
        self._register(agent, order_id, qty)
        self._publish(events)
        return order_id

    def cancel(self, symbol: str, order_id: int) -> None:
        self._publish(self.engine.cancel(self.now, symbol, order_id))

    def new_episode(self, kind: str, symbol: str, accounts: list[str]) -> Episode:
        episode = Episode(
            episode_id=f"s{self.seed}-d{self.day}-e{len(self.episodes)}",
            type=kind,
            symbol=symbol,
            owner=self.population.owner_of[accounts[0]],
            accounts=accounts,
            start_ns=self.now,
            end_ns=self.now,
        )
        self.episodes.append(episode)
        return episode

    def _register(self, agent: Agent, order_id: int, qty: int) -> None:
        self._owners[order_id] = agent
        agent.live[order_id] = qty

    def _publish(self, events: list[Event]) -> None:
        sink = self.sink
        owners = self._owners
        for event in events:
            sink(event)
            if isinstance(event, Trade):
                self.last_price[event.symbol] = event.price
                owners[event.buy_order_id].on_fill(self, event.buy_order_id, event)
                owners[event.sell_order_id].on_fill(self, event.sell_order_id, event)
            elif isinstance(event, OrderCancelled):
                owners[event.order_id].on_cancelled(self, event)
        self.event_count += len(events)

    # -- main loop ------------------------------------------------------------------

    def run(self, speed: float | None = None) -> None:
        """Run the session. `speed=None` replays at max speed; otherwise sim seconds per
        wall second (1.0 = realtime)."""
        for agent in self.agents:
            agent.start(self)
        for symbol in self.symbols:
            self.at(self.open_ns + SECOND, partial(self._fair_tick, symbol))
            self.at(self.open_ns, partial(self._snapshot, symbol))

        wall_start = time.perf_counter()
        queue = self._queue
        while queue:
            ts, _, fn = heapq.heappop(queue)
            if ts >= self.close_ns:
                break
            if speed is not None:
                lag = (ts - self.open_ns) / SECOND / speed - (time.perf_counter() - wall_start)
                if lag > 0:
                    time.sleep(lag)
            self.now = ts
            fn()

        self.now = self.close_ns
        self._publish(self.engine.expire_all(self.close_ns))
        for symbol in self.symbols:
            self.sink(self.engine.snapshot(self.close_ns, symbol, self.config.snapshot_depth))
            self.event_count += 1

    def _fair_tick(self, symbol: str) -> None:
        self.fair[symbol] += self._fair_rng[symbol].gauss(0, self._volatility[symbol])
        self.after(SECOND, partial(self._fair_tick, symbol))

    def _snapshot(self, symbol: str) -> None:
        self.sink(self.engine.snapshot(self.now, symbol, self.config.snapshot_depth))
        self.event_count += 1
        self.after(self.config.snapshot_interval_ms * MS, partial(self._snapshot, symbol))

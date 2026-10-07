from collections import defaultdict

import msgspec
import pytest

from tripwire.common.codec import stream_hash
from tripwire.common.events import (
    BookSnapshot,
    CancelReason,
    Event,
    OrderAccepted,
    OrderCancelled,
    Trade,
)
from tripwire.sim.config import CLEAN_DAY, SimConfig
from tripwire.sim.day import DayResult, simulate_day
from tripwire.sim.labels import read_labels, write_labels
from tripwire.sim.population import build_population, reference_data

SHORT = SimConfig(session_minutes=40)


def run(config: SimConfig = SHORT, seed: int = 3, day: int = 0) -> tuple[list[Event], DayResult]:
    events: list[Event] = []
    result = simulate_day(config, seed, day, events.append)
    return events, result


@pytest.fixture(scope="module")
def day() -> tuple[list[Event], DayResult]:
    return run()


def test_same_seed_gives_identical_stream(day: tuple[list[Event], DayResult]) -> None:
    events, _ = day
    again, _ = run()
    assert stream_hash(events) == stream_hash(again)


def test_different_seed_gives_different_stream(day: tuple[list[Event], DayResult]) -> None:
    events, _ = day
    other, _ = run(seed=4)
    assert stream_hash(events) != stream_hash(other)


def test_timestamps_are_monotonic_and_inside_session(day: tuple[list[Event], DayResult]) -> None:
    events, result = day
    stamps = [e.ts for e in events]
    assert stamps == sorted(stamps)
    assert stamps[0] >= result.open_ns and stamps[-1] <= result.close_ns


def test_every_order_ends_filled_or_cancelled(day: tuple[list[Event], DayResult]) -> None:
    events, _ = day
    remaining: dict[int, int] = {}
    for e in events:
        if isinstance(e, OrderAccepted):
            remaining[e.order_id] = e.qty
        elif isinstance(e, Trade):
            remaining[e.buy_order_id] -= e.qty
            remaining[e.sell_order_id] -= e.qty
        elif isinstance(e, OrderCancelled):
            remaining[e.order_id] -= e.qty
    assert all(qty == 0 for qty in remaining.values())


def test_book_is_never_crossed(day: tuple[list[Event], DayResult]) -> None:
    events, _ = day
    for e in events:
        if isinstance(e, BookSnapshot) and e.bids and e.asks:
            assert e.bids[0][0] < e.asks[0][0]


def test_all_manipulation_types_are_injected(day: tuple[list[Event], DayResult]) -> None:
    _, result = day
    kinds = {ep.type for ep in result.episodes}
    assert kinds == {"spoofing", "layering", "wash", "marking_close"}


def test_episode_orders_exist_and_belong_to_episode_accounts(
    day: tuple[list[Event], DayResult],
) -> None:
    events, result = day
    accepted = {e.order_id: e for e in events if isinstance(e, OrderAccepted)}
    population = build_population(SHORT)
    for ep in result.episodes:
        assert ep.start_ns <= ep.end_ns
        for order_id in ep.order_ids:
            order = accepted[order_id]
            assert order.account in ep.accounts
            assert order.symbol == ep.symbol
            assert ep.start_ns <= order.ts <= ep.end_ns
        assert {population.owner_of[a] for a in ep.accounts} == {ep.owner}


def test_wash_episodes_trade_between_their_own_accounts(
    day: tuple[list[Event], DayResult],
) -> None:
    events, result = day
    for ep in (ep for ep in result.episodes if ep.type == "wash"):
        ids = set(ep.order_ids)
        crossed = [
            t
            for t in events
            if isinstance(t, Trade) and t.buy_order_id in ids and t.sell_order_id in ids
        ]
        assert crossed
        assert all({t.buy_account, t.sell_account} == set(ep.accounts) for t in crossed)


def test_spoof_orders_are_cancelled_quickly_after_genuine_fill(
    day: tuple[list[Event], DayResult],
) -> None:
    """At least one spoofing cycle should show the textbook shape: the genuine order
    fills, then the large order is cancelled within 50ms."""
    events, result = day
    accepted = {e.order_id: e for e in events if isinstance(e, OrderAccepted)}
    fill_ts: dict[int, int] = {}
    cancel_ts: dict[int, int] = {}
    for e in events:
        if isinstance(e, Trade):
            fill_ts.setdefault(e.buy_order_id, e.ts)
            fill_ts.setdefault(e.sell_order_id, e.ts)
        elif isinstance(e, OrderCancelled) and e.reason is CancelReason.USER:
            cancel_ts[e.order_id] = e.ts
    quick = 0
    for ep in (ep for ep in result.episodes if ep.type == "spoofing"):
        orders = [accepted[i] for i in ep.order_ids]
        big = [o for o in orders if o.qty >= 3_000]
        small = [o for o in orders if o.qty < 3_000 and o.order_id in fill_ts]
        for b in big:
            if b.order_id not in cancel_ts:
                continue
            for s in small:
                if (
                    s.side is not b.side
                    and 0 <= cancel_ts[b.order_id] - fill_ts[s.order_id] <= 50_000_000
                ):
                    quick += 1
    assert quick > 0


def test_market_makers_are_cancel_heavy(day: tuple[list[Event], DayResult]) -> None:
    """The key false-positive case: legit market makers cancel nearly every order."""
    events, _ = day
    population = build_population(SHORT)
    orders: dict[str, int] = defaultdict(int)
    user_cancels: dict[str, int] = defaultdict(int)
    for e in events:
        if isinstance(e, OrderAccepted):
            orders[e.account] += 1
        elif isinstance(e, OrderCancelled) and e.reason is CancelReason.USER:
            user_cancels[e.account] += 1
    for mm in population.market_makers:
        assert user_cancels[mm] / orders[mm] > 0.9


def test_clean_day_has_no_episodes() -> None:
    _, result = run(msgspec.structs.replace(SHORT, episodes=CLEAN_DAY))
    assert result.episodes == []


def test_days_use_disjoint_order_ids() -> None:
    config = SimConfig(session_minutes=5)
    ids = []
    for day_index in (0, 1):
        events, _ = run(config, day=day_index)
        ids.append({e.order_id for e in events if isinstance(e, OrderAccepted)})
    assert not ids[0] & ids[1]


def test_population_hides_roles_and_groups_owners() -> None:
    config = SimConfig()
    population = build_population(config)
    owners_with_many = defaultdict(list)
    for account, owner in population.owner_of.items():
        owners_with_many[owner].append(account)
    multi = [accs for accs in owners_with_many.values() if len(accs) > 1]
    # institutional owner + every wash owner hold several accounts
    assert len(multi) == 1 + config.wash_owners
    reference = reference_data(config, population)
    assert all(rec.account.startswith("ACC") for rec in reference.accounts)
    assert set(msgspec.structs.asdict(reference.accounts[0])) == {"account", "owner"}


def test_labels_round_trip(tmp_path, day: tuple[list[Event], DayResult]) -> None:  # type: ignore[no-untyped-def]
    _, result = day
    path = tmp_path / "labels.jsonl"
    write_labels(path, result.episodes)
    assert read_labels(path) == result.episodes

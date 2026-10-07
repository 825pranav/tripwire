import pytest

from tripwire.common.codec import decode, encode
from tripwire.common.events import (
    BookSnapshot,
    CancelReason,
    Event,
    OrderAccepted,
    OrderCancelled,
    Side,
    Trade,
)
from tripwire.exchange.engine import MatchingEngine

BUY, SELL = Side.BUY, Side.SELL


def trades(events: list[Event]) -> list[Trade]:
    return [e for e in events if isinstance(e, Trade)]


def cancels(events: list[Event]) -> list[OrderCancelled]:
    return [e for e in events if isinstance(e, OrderCancelled)]


@pytest.fixture
def engine() -> MatchingEngine:
    return MatchingEngine(["AAA", "BBB"])


def test_resting_limit_order_is_accepted_and_rests(engine: MatchingEngine) -> None:
    order_id, events = engine.submit_limit(1, "AAA", "a1", BUY, 100, 300)
    assert len(events) == 1
    accepted = events[0]
    assert isinstance(accepted, OrderAccepted)
    assert accepted.order_id == order_id and accepted.qty == 300
    assert engine.books["AAA"].best_bid() == 100
    assert engine.books["AAA"].best_ask() is None


def test_better_price_fills_before_earlier_time(engine: MatchingEngine) -> None:
    early, _ = engine.submit_limit(1, "AAA", "a1", SELL, 102, 100)
    better, _ = engine.submit_limit(2, "AAA", "a2", SELL, 101, 100)
    _, events = engine.submit_limit(3, "AAA", "b1", BUY, 105, 100)
    [trade] = trades(events)
    assert trade.sell_order_id == better
    assert trade.price == 101  # trades print at the resting price
    assert early in engine.books["AAA"].orders


def test_time_priority_within_a_level(engine: MatchingEngine) -> None:
    first, _ = engine.submit_limit(1, "AAA", "a1", BUY, 100, 100)
    second, _ = engine.submit_limit(2, "AAA", "a2", BUY, 100, 100)
    third, _ = engine.submit_limit(3, "AAA", "a3", BUY, 100, 100)
    _, events = engine.submit_limit(4, "AAA", "s1", SELL, 100, 250)
    fills = trades(events)
    assert [t.buy_order_id for t in fills] == [first, second, third]
    assert [t.qty for t in fills] == [100, 100, 50]
    assert engine.books["AAA"].orders[third].qty == 50


def test_partial_fill_of_incoming_rests_remainder(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    order_id, events = engine.submit_limit(2, "AAA", "b1", BUY, 101, 300)
    [trade] = trades(events)
    assert trade.qty == 100 and trade.aggressor_side is BUY
    resting = engine.books["AAA"].orders[order_id]
    assert resting.qty == 200 and resting.price == 101
    assert engine.books["AAA"].best_ask() is None
    assert engine.books["AAA"].best_bid() == 101


def test_limit_order_stops_at_its_limit_price(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    engine.submit_limit(2, "AAA", "a2", SELL, 103, 100)
    order_id, events = engine.submit_limit(3, "AAA", "b1", BUY, 102, 300)
    assert [t.price for t in trades(events)] == [101]
    assert engine.books["AAA"].orders[order_id].qty == 200
    assert engine.books["AAA"].best_ask() == 103


def test_market_order_sweeps_multiple_levels(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    engine.submit_limit(2, "AAA", "a2", SELL, 102, 200)
    engine.submit_limit(3, "AAA", "a3", SELL, 104, 100)
    order_id, events = engine.submit_market(4, "AAA", "b1", BUY, 350)
    fills = trades(events)
    assert [(t.price, t.qty) for t in fills] == [(101, 100), (102, 200), (104, 50)]
    assert all(t.buy_order_id == order_id for t in fills)
    assert engine.books["AAA"].orders  # 50 left at 104
    assert engine.books["AAA"].best_ask() == 104
    assert not cancels(events)


def test_market_order_remainder_is_cancelled(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", BUY, 99, 100)
    order_id, events = engine.submit_market(2, "AAA", "s1", SELL, 300)
    assert sum(t.qty for t in trades(events)) == 100
    [cancel] = cancels(events)
    assert cancel.order_id == order_id
    assert cancel.qty == 200 and cancel.reason is CancelReason.UNFILLED
    assert order_id not in engine.books["AAA"].orders


def test_market_order_on_empty_book(engine: MatchingEngine) -> None:
    _, events = engine.submit_market(1, "AAA", "b1", BUY, 100)
    assert [type(e) for e in events] == [OrderAccepted, OrderCancelled]


def test_cancel_removes_order_and_level(engine: MatchingEngine) -> None:
    order_id, _ = engine.submit_limit(1, "AAA", "a1", BUY, 100, 300)
    [cancel] = engine.cancel(2, "AAA", order_id)
    assert isinstance(cancel, OrderCancelled)
    assert cancel.qty == 300 and cancel.reason is CancelReason.USER
    assert engine.books["AAA"].best_bid() is None
    assert engine.cancel(3, "AAA", order_id) == []  # already gone


def test_cancel_after_partial_fill_reports_remaining(engine: MatchingEngine) -> None:
    order_id, _ = engine.submit_limit(1, "AAA", "a1", SELL, 101, 300)
    engine.submit_market(2, "AAA", "b1", BUY, 100)
    [cancel] = engine.cancel(3, "AAA", order_id)
    assert isinstance(cancel, OrderCancelled) and cancel.qty == 200


def test_cancel_of_filled_or_unknown_order_is_noop(engine: MatchingEngine) -> None:
    order_id, _ = engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    engine.submit_market(2, "AAA", "b1", BUY, 100)
    assert engine.cancel(3, "AAA", order_id) == []
    assert engine.cancel(3, "AAA", 999_999) == []


def test_cancel_leaves_queue_order_of_others(engine: MatchingEngine) -> None:
    first, _ = engine.submit_limit(1, "AAA", "a1", BUY, 100, 100)
    second, _ = engine.submit_limit(2, "AAA", "a2", BUY, 100, 100)
    third, _ = engine.submit_limit(3, "AAA", "a3", BUY, 100, 100)
    engine.cancel(4, "AAA", second)
    _, events = engine.submit_market(5, "AAA", "s1", SELL, 200)
    assert [t.buy_order_id for t in trades(events)] == [first, third]


def test_self_trade_prevention_cancels_resting(engine: MatchingEngine) -> None:
    own, _ = engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    other, _ = engine.submit_limit(2, "AAA", "a2", SELL, 101, 100)
    _, events = engine.submit_limit(3, "AAA", "a1", BUY, 101, 100)
    [cancel] = cancels(events)
    assert cancel.order_id == own and cancel.reason is CancelReason.SELF_TRADE
    [trade] = trades(events)
    assert trade.sell_order_id == other


def test_same_owner_different_accounts_can_trade(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "owner1-x", SELL, 101, 100)
    _, events = engine.submit_limit(2, "AAA", "owner1-y", BUY, 101, 100)
    [trade] = trades(events)
    assert (trade.buy_account, trade.sell_account) == ("owner1-y", "owner1-x")


def test_symbols_are_isolated(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    _, events = engine.submit_market(2, "BBB", "b1", BUY, 100)
    assert not trades(events)
    assert engine.books["AAA"].best_ask() == 101


def test_depth_snapshot_aggregates_levels(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", BUY, 100, 100)
    engine.submit_limit(2, "AAA", "a2", BUY, 100, 200)
    engine.submit_limit(3, "AAA", "a3", BUY, 99, 50)
    engine.submit_limit(4, "AAA", "a4", SELL, 102, 70)
    engine.submit_limit(5, "AAA", "a5", SELL, 103, 30)
    snap = engine.snapshot(6, "AAA", depth=1)
    assert snap == BookSnapshot(ts=6, symbol="AAA", bids=((100, 300),), asks=((102, 70),))
    full = engine.snapshot(6, "AAA", depth=10)
    assert full.bids == ((100, 300), (99, 50))
    assert full.asks == ((102, 70), (103, 30))


def test_expire_all_cancels_everything(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", BUY, 100, 100)
    engine.submit_limit(2, "BBB", "a2", SELL, 200, 100)
    expired = engine.expire_all(10)
    assert len(expired) == 2
    assert all(isinstance(e, OrderCancelled) and e.reason is CancelReason.EXPIRED for e in expired)
    assert not engine.books["AAA"].orders and not engine.books["BBB"].orders


def test_order_ids_are_sequential_from_start() -> None:
    engine = MatchingEngine(["AAA"], first_order_id=5_000)
    first, _ = engine.submit_limit(1, "AAA", "a1", BUY, 100, 100)
    second, _ = engine.submit_market(2, "AAA", "a2", SELL, 100)
    assert (first, second) == (5_000, 5_001)


@pytest.mark.parametrize("qty", [0, -100])
def test_rejects_non_positive_quantity(engine: MatchingEngine, qty: int) -> None:
    with pytest.raises(ValueError):
        engine.submit_limit(1, "AAA", "a1", BUY, 100, qty)


def test_events_round_trip_through_msgpack(engine: MatchingEngine) -> None:
    engine.submit_limit(1, "AAA", "a1", SELL, 101, 100)
    _, events = engine.submit_market(2, "AAA", "b1", BUY, 300)
    events.append(engine.snapshot(3, "AAA"))
    assert [decode(encode(e)) for e in events] == events

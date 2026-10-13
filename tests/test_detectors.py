"""Each rule gets a crafted positive scenario and negatives, with the legitimate market
maker as the main negative."""

from collections import Counter

import msgspec
import pytest
from scenario import BUY, SELL, Scenario, of_type

from tripwire.common.events import Event
from tripwire.detectors.config import DetectorConfig, load_config
from tripwire.detectors.suite import DetectorSuite
from tripwire.sim.config import SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data


def test_config_file_matches_documented_defaults() -> None:
    assert load_config() == DetectorConfig()


# -- spoofing -----------------------------------------------------------------------


def spoof(s: Scenario, cancel_after_fill_ms: float = 10, big_qty: int = 5_000) -> tuple[int, int]:
    s.background_book()
    big = s.limit("SPF", BUY, 9_999, big_qty)  # joins the best bid
    s.wait(5)
    genuine = s.limit("SPF", SELL, 10_000, 200)  # steps inside the spread
    s.wait(300)
    s.market("NOISE", BUY, 200)  # lifts the genuine sell
    s.wait(cancel_after_fill_ms)
    s.cancel(big)
    return big, genuine


def test_spoofing_positive() -> None:
    s = Scenario()
    big, genuine = spoof(s)
    [alert] = s.alerts()
    assert alert.type == "spoofing"
    assert alert.accounts == ("SPF",)
    assert alert.order_ids == (big, genuine)
    assert alert.features["cancel_latency_ms"] == 10
    assert 0 < alert.score <= 1


def test_spoofing_negative_slow_cancel() -> None:
    s = Scenario()
    spoof(s, cancel_after_fill_ms=500)
    assert s.alerts() == []


def test_spoofing_negative_order_not_large() -> None:
    s = Scenario()
    spoof(s, big_qty=1_000)
    assert s.alerts() == []


def test_spoofing_negative_market_maker_with_inventory_skew() -> None:
    """A long market maker shows a big ask, its bid gets hit and it requotes within 2ms.
    Same timing as spoofing, but its interest is two-sided."""
    s = Scenario()
    s.background_book("MM2")
    bid1 = s.limit("MM1", BUY, 9_999, 300)
    ask1 = s.limit("MM1", SELL, 10_000, 900)
    bid2 = s.limit("MM1", BUY, 9_998, 600)
    ask2 = s.limit("MM1", SELL, 10_002, 3_000)
    s.snapshot()
    s.wait(800)
    s.market("NOISE", SELL, 300)  # hits MM1's bid
    s.wait(2)
    s.cancel(bid1, ask1, bid2, ask2)
    assert s.alerts() == []


def test_spoofing_negative_institutional_large_resting_order() -> None:
    s = Scenario()
    s.background_book()
    big = s.limit("INST", BUY, 9_997, 5_000)
    s.wait(5 * 60_000)
    s.cancel(big)
    assert s.alerts() == []


# -- layering -----------------------------------------------------------------------


def test_layering_positive() -> None:
    s = Scenario()
    s.background_book()
    layers = [s.limit("LYR", BUY, 9_999 - i, 1_000) for i in range(4)]
    s.wait(5)
    genuine = s.limit("LYR", SELL, 10_000, 200)
    s.wait(200)
    s.market("NOISE", BUY, 200)
    s.wait(3)
    s.cancel(*layers)
    s.wait(1_000)
    s.snapshot()  # time moves on, the burst window closes
    alerts = s.alerts()
    assert [a.type for a in alerts] == ["layering"]  # layers are too small to be spoofing
    [alert] = alerts
    assert alert.order_ids == (*layers, genuine)
    assert alert.features["levels"] == 4


def test_layering_negative_market_maker_requote() -> None:
    s = Scenario()
    s.background_book("MM2")
    quotes = [
        s.limit("MM1", BUY, 9_999, 500),
        s.limit("MM1", SELL, 10_000, 500),
        s.limit("MM1", BUY, 9_998, 1_000),
        s.limit("MM1", SELL, 10_002, 1_000),
    ]
    s.wait(500)
    s.market("NOISE", BUY, 500)
    s.wait(2)
    s.cancel(*quotes)
    s.wait(1_000)
    s.snapshot()
    assert s.alerts() == []


def test_layering_negative_only_two_levels() -> None:
    s = Scenario()
    s.background_book()
    layers = [s.limit("LYR", BUY, 9_999 - i, 2_000) for i in range(2)]
    s.limit("LYR", SELL, 10_000, 200)
    s.wait(200)
    s.market("NOISE", BUY, 200)
    s.wait(3)
    s.cancel(*layers)
    s.wait(1_000)
    s.snapshot()
    assert of_type(s.alerts(), "layering") == []


# -- wash ---------------------------------------------------------------------------

OWNERS = {"W1": "OWN-A", "W2": "OWN-A", "X1": "OWN-X", "Y1": "OWN-Y", "MM1": "OWN-M"}


def cross(s: Scenario, seller: str, buyer: str, rest_ms: float, qty: int = 300) -> None:
    s.limit(seller, SELL, 10_000, qty)
    s.wait(rest_ms)
    s.limit(buyer, BUY, 10_000, qty)
    s.wait(10_000)


def test_wash_positive() -> None:
    s = Scenario(OWNERS)
    s.background_book()
    for i in range(4):
        cross(s, *(("W1", "W2") if i % 2 else ("W2", "W1")), rest_ms=5)
    alerts = s.alerts()
    [alert] = alerts  # fires once, then stays quiet for the window
    assert alert.type == "wash"
    assert alert.accounts == ("W1", "W2")
    assert alert.features["same_owner_trades"] == 3


def test_wash_negative_different_owners() -> None:
    s = Scenario(OWNERS)
    s.background_book()
    for _ in range(4):
        cross(s, "X1", "Y1", rest_ms=5)
    assert s.alerts() == []


def test_wash_negative_too_few_crosses() -> None:
    s = Scenario(OWNERS)
    s.background_book()
    for _ in range(2):
        cross(s, "W1", "W2", rest_ms=5)
    assert s.alerts() == []


def test_wash_negative_related_accounts_cross_by_accident() -> None:
    """Same owner, but the passive order had been resting for minutes."""
    s = Scenario(OWNERS)
    s.background_book()
    for _ in range(4):
        cross(s, "W1", "W2", rest_ms=3 * 60_000)
    assert s.alerts() == []


# -- marking the close --------------------------------------------------------------


def deep_asks(s: Scenario) -> None:
    for i in range(30):
        s.limit("MM1", SELL, 10_001 + i, 500)
        s.limit("MM1", BUY, 9_999 - i, 500)
    s.snapshot()


def test_marking_close_positive() -> None:
    s = Scenario()
    deep_asks(s)
    s.at_minutes_before_close(4)
    s.market("NOISE", SELL, 200)  # some ordinary volume in the window
    for _ in range(10):
        best_ask = s.engine.books["XYZ"].best_ask()
        assert best_ask is not None
        s.limit("CLS", BUY, best_ask + 2, 600)
        s.wait(15_000)
    s.close_session()
    [alert] = s.alerts()
    assert alert.type == "marking_close"
    assert alert.accounts == ("CLS",)
    assert alert.features["impact_ticks"] >= 4
    assert alert.features["direction"] == 1


def test_marking_close_negative_twap_rebalance() -> None:
    s = Scenario()
    deep_asks(s)
    s.at_minutes_before_close(25)
    for _ in range(40):
        best_ask = s.engine.books["XYZ"].best_ask()
        assert best_ask is not None
        s.limit("INST", BUY, best_ask, 100)
        s.wait(36_000)
    s.close_session()
    assert s.alerts() == []


def test_marking_close_negative_same_buying_mid_session() -> None:
    s = Scenario()
    deep_asks(s)
    s.at_minutes_before_close(60)
    for _ in range(10):
        best_ask = s.engine.books["XYZ"].best_ask()
        assert best_ask is not None
        s.limit("CLS", BUY, best_ask + 2, 600)
        s.wait(15_000)
    s.close_session()
    assert s.alerts() == []


# -- simulated market ---------------------------------------------------------------


@pytest.mark.parametrize("seed", [101, 102])
def test_no_alerts_on_market_makers_or_institutions_in_simulated_days(seed: int) -> None:
    config = SimConfig(session_minutes=60)
    population = build_population(config)
    events: list[Event] = []
    simulate_day(config, seed, 0, events.append, population=population)
    alerts = DetectorSuite(reference_data(config, population)).run(events)
    flagged = Counter(acc for a in alerts for acc in a.accounts)
    for account in population.market_makers + population.institutional:
        assert flagged[account] == 0


def test_suite_is_deterministic() -> None:
    config = msgspec.structs.replace(SimConfig(), session_minutes=30)
    population = build_population(config)
    events: list[Event] = []
    simulate_day(config, 5, 0, events.append, population=population)
    reference = reference_data(config, population)
    assert DetectorSuite(reference).run(events) == DetectorSuite(reference).run(events)


def test_alerts_do_not_depend_on_how_symbols_are_interleaved() -> None:
    """A Kafka consumer sees each symbol in order, but symbols can drift apart in time
    (e.g. while catching up on a backlog). Processing a day symbol by symbol must give
    the same alerts as processing it in global time order."""
    from tripwire.ml.model import load_model
    from tripwire.ml.scorer import MLScorer

    config = SimConfig(session_minutes=45)
    population = build_population(config)
    reference = reference_data(config, population)
    events: list[Event] = []
    simulate_day(config, 11, 0, events.append, population=population)
    by_symbol = sorted(events, key=lambda e: e.symbol)  # stable: keeps per-symbol order

    def detect(stream: list[Event]) -> set[tuple[object, ...]]:
        alerts = DetectorSuite(reference).run(stream) + MLScorer(load_model(), reference).run(
            stream
        )
        return {(a.type, a.symbol, a.accounts, a.order_ids, a.window_start_ns) for a in alerts}

    in_order = detect(events)
    assert in_order
    assert detect(by_symbol) == in_order

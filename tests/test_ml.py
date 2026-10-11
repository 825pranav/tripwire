import msgspec
import numpy as np
import pytest
from scenario import BUY, SELL, Scenario

from tripwire.common.alerts import Alert
from tripwire.common.events import Event
from tripwire.ml.features import FEATURES, WindowFeatures, WindowRow
from tripwire.ml.fusion import fuse
from tripwire.ml.model import AnomalyModel, load_model
from tripwire.ml.scorer import MLScorer, ScoredWindow
from tripwire.sim.config import CLEAN_DAY, SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data

OWNERS = {"W1": "OWN-A", "W2": "OWN-A", "MM1": "OWN-M", "N1": "OWN-N"}


def feature(row: WindowRow, name: str) -> float:
    return row.features[FEATURES.index(name)]


def wash_scenario() -> Scenario:
    s = Scenario(OWNERS)
    s.background_book()
    for _ in range(6):
        s.limit("W1", SELL, 10_000, 500)
        s.wait(5)
        s.limit("W2", BUY, 10_000, 500)
        s.wait(20_000)
    s.wait(10 * 60_000)
    s.snapshot()  # pushes event time past the window boundary
    return s


def test_window_features_capture_same_owner_trading() -> None:
    s = wash_scenario()
    rows = WindowFeatures(s.reference).run(s.events)
    w1 = [r for r in rows if r.account == "W1"]
    assert w1
    assert all(feature(r, "self_trade_ratio") == 1.0 for r in w1)
    assert all(r.end_ns - r.start_ns == 300 * 1_000_000_000 for r in rows)
    for row in rows:
        assert 0.0 <= feature(row, "cancel_ratio") <= 1.0
        assert len(row.features) == len(FEATURES)


def test_scorer_flags_wash_window() -> None:
    s = wash_scenario()
    alerts = MLScorer(load_model(), s.reference).run(s.events)
    flagged = {a.accounts[0] for a in alerts}
    assert flagged & {"W1", "W2"}
    assert all(a.type == "ml_anomaly" and 0.5 <= a.score <= 1 for a in alerts)


def test_clean_day_alert_rate_matches_calibration() -> None:
    config = msgspec.structs.replace(SimConfig(session_minutes=60), episodes=CLEAN_DAY)
    population = build_population(config)
    events: list[Event] = []
    simulate_day(config, 777, 0, events.append, population=population)
    scorer = MLScorer(load_model(), reference_data(config, population))
    alerts = scorer.run(events)
    assert len(alerts) / len(scorer.scored) < 0.02  # threshold is the clean p99.5


def make_alert(score: float, account: str = "A", start: int = 0, end: int = 10) -> Alert:
    return Alert(
        alert_id="r",
        type="spoofing",
        symbol="XYZ",
        accounts=(account,),
        window_start_ns=start,
        window_end_ns=end,
        order_ids=(1,),
        score=score,
        features={},
        emitted_ts=end,
    )


def window(score: float, account: str = "A") -> ScoredWindow:
    row = WindowRow(account, "XYZ", 0, 100, tuple(0.0 for _ in FEATURES), (1,))
    return ScoredWindow(row, score)


@pytest.mark.parametrize(
    ("rule", "ml", "flagged", "kind"),
    [
        (0.0, 0.55, False, None),  # borderline ML alone is dropped
        (0.0, 0.8, True, "ml_anomaly"),  # strong ML alone survives
        (0.7, 0.0, True, "spoofing"),  # a rule hit survives
        (0.3, 0.45, True, "spoofing"),  # two weak signals agree: 1 - 0.7 * 0.55 = 0.615
    ],
)
def test_fusion_noisy_or(rule: float, ml: float, flagged: bool, kind: str | None) -> None:
    rules = [make_alert(rule)] if rule else []
    fused = fuse(rules, [window(ml)])
    assert bool(fused) is flagged
    if fused:
        assert fused[0].type == kind


def test_fusion_ignores_rule_alerts_for_other_accounts() -> None:
    assert fuse([make_alert(0.9, account="B")], [window(0.1)]) == []


def test_model_normalisation_is_monotonic_with_threshold_at_half() -> None:
    model = load_model()
    raw = np.array([model.median - 0.1, model.median, model.threshold, model.top + 1])
    norm = model.normalise(raw)
    assert list(norm) == sorted(norm)
    assert norm[2] == pytest.approx(0.5)
    assert norm[0] == 0 and norm[-1] == 1


def test_model_with_stale_feature_set_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    model = load_model()
    stale = AnomalyModel(model.forest, ("old",), model.median, model.threshold, model.top)
    path = tmp_path / "m.joblib"
    stale.save(path)
    with pytest.raises(ValueError, match="make train"):
        load_model(path)

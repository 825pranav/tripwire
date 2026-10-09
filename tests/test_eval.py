import copy

from tripwire.common.alerts import Alert
from tripwire.eval.harness import count, evaluate
from tripwire.eval.matching import matches
from tripwire.eval.report import check_against_baseline, diff, markdown
from tripwire.sim.config import SimConfig
from tripwire.sim.labels import Episode

S = 1_000_000_000
OWNERS = {"A1": "O1", "A2": "O1", "B1": "O2"}


def episode(kind: str = "spoofing", accounts: tuple[str, ...] = ("A1",)) -> Episode:
    return Episode("e0", kind, "XYZ", OWNERS[accounts[0]], list(accounts), 100 * S, 200 * S, [1])


def alert(
    kind: str = "spoofing", accounts: tuple[str, ...] = ("A1",), start: int = 150, end: int = 160
) -> Alert:
    return Alert(
        alert_id="x",
        type=kind,
        symbol="XYZ",
        accounts=accounts,
        window_start_ns=start * S,
        window_end_ns=end * S,
        order_ids=(1,),
        score=0.5,
        features={},
        emitted_ts=end * S,
    )


def test_match_requires_type_symbol_account_and_overlap() -> None:
    ep = episode()
    assert matches(alert(), ep, OWNERS)
    assert not matches(alert(kind="wash"), ep, OWNERS)
    assert not matches(alert(accounts=("B1",)), ep, OWNERS)
    assert not matches(alert(start=300, end=310), ep, OWNERS)
    assert matches(alert(start=220, end=225), ep, OWNERS)  # within 30s tolerance
    assert matches(alert(accounts=("A2",)), ep, OWNERS)  # same beneficial owner
    assert matches(alert(kind="ml_anomaly"), ep, OWNERS)  # untyped ML alert


def test_count_per_type_and_overall() -> None:
    episodes = [episode(), episode("wash", ("A1", "A2"))]
    alerts = [alert(), alert(), alert(accounts=("B1",)), alert(kind="ml_anomaly", accounts=("B1",))]
    counts = count(alerts, episodes, OWNERS)
    assert (counts["spoofing"].tp_alerts, counts["spoofing"].alerts) == (2, 3)
    assert (counts["spoofing"].detected, counts["spoofing"].episodes) == (1, 1)
    assert (counts["wash"].detected, counts["wash"].episodes) == (0, 1)
    assert (counts["overall"].alerts, counts["overall"].tp_alerts) == (4, 2)
    assert (counts["overall"].detected, counts["overall"].episodes) == (1, 2)


def test_untyped_alert_is_attributed_to_matched_episode_type() -> None:
    counts = count([alert(kind="ml_anomaly")], [episode()], OWNERS)
    assert counts["spoofing"].tp_alerts == 1
    assert counts["overall"].tp_alerts == 1


def test_evaluate_short_days_runs_end_to_end() -> None:
    results = evaluate(2, SimConfig(session_minutes=40), first_seed=500)
    rules = results["methods"]["rules"]  # type: ignore[index]
    assert rules["overall"]["episodes"] > 0
    assert "Eval over 2 simulated days" in markdown(results)  # type: ignore[arg-type]


def baseline_like() -> dict:  # type: ignore[type-arg]
    return {
        "methods": {"rules": {"spoofing": {"f1": 0.9}, "wash": {"f1": None}}},
        "throughput": {"detector_events_per_s": 100_000},
    }


def test_baseline_check_flags_f1_and_throughput_drops() -> None:
    base = baseline_like()
    same = copy.deepcopy(base)
    assert check_against_baseline(same, base) == []
    worse = copy.deepcopy(base)
    worse["methods"]["rules"]["spoofing"]["f1"] = 0.84
    worse["throughput"]["detector_events_per_s"] = 79_000
    problems = check_against_baseline(worse, base)
    assert len(problems) == 2
    small = copy.deepcopy(base)
    small["methods"]["rules"]["spoofing"]["f1"] = 0.86
    small["throughput"]["detector_events_per_s"] = 81_000
    assert check_against_baseline(small, base) == []
    assert "0.900 -> 0.860" in diff(base, small)

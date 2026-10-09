"""In-process evaluation: simulate seeded days, run the detectors on the same event stream
the Kafka path would carry, and score alerts against the ground-truth labels.

Labels go through the label files on purpose (written by the simulator, read back here)
so the eval exercises the same isolation as the streaming system.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import msgspec

from tripwire.common.alerts import Alert
from tripwire.common.events import Event
from tripwire.common.reference import ReferenceData
from tripwire.detectors.suite import DetectorSuite
from tripwire.eval.matching import EPISODE_TYPES, matches
from tripwire.sim.config import SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.labels import Episode, labels_path, read_labels, write_labels
from tripwire.sim.population import Population, build_population, reference_data

EVAL_SEED_BASE = 1000  # tuning used seeds 0-9; evaluation days never reuse them

# A method turns one day's events (plus the rule alerts already computed) into alerts.
Method = Callable[[Sequence[Event], list[Alert]], list[Alert]]


class Counts(msgspec.Struct):
    tp_alerts: int = 0
    alerts: int = 0
    detected: int = 0
    episodes: int = 0

    def add(self, other: Counts) -> None:
        self.tp_alerts += other.tp_alerts
        self.alerts += other.alerts
        self.detected += other.detected
        self.episodes += other.episodes

    def metrics(self) -> dict[str, float | int | None]:
        precision = self.tp_alerts / self.alerts if self.alerts else None
        recall = self.detected / self.episodes if self.episodes else None
        f1 = None
        if precision is not None and recall is not None:
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "precision": _round(precision),
            "recall": _round(recall),
            "f1": _round(f1),
            "alerts": self.alerts,
            "tp_alerts": self.tp_alerts,
            "episodes": self.episodes,
            "detected": self.detected,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def count(
    alerts: Sequence[Alert], episodes: Sequence[Episode], owner_of: dict[str, str]
) -> dict[str, Counts]:
    """Counts per episode type plus "overall".

    Typed (rule) alerts count towards their own type. Untyped ML alerts are attributed to
    the type of the episode they match; unmatched ones only count in "overall".
    """
    result = {kind: Counts() for kind in (*EPISODE_TYPES, "overall")}
    overall = result["overall"]
    for alert in alerts:
        hit = next((e for e in episodes if matches(alert, e, owner_of)), None)
        overall.alerts += 1
        overall.tp_alerts += hit is not None
        kind = alert.type if alert.type in result else (hit.type if hit else None)
        if kind is not None:
            result[kind].alerts += 1
            result[kind].tp_alerts += hit is not None
    for episode in episodes:
        found = any(matches(a, episode, owner_of) for a in alerts)
        for key in (episode.type, "overall"):
            result[key].episodes += 1
            result[key].detected += found
    return result


class DayRun(msgspec.Struct):
    seed: int
    day: int
    events: int
    episodes: int
    sim_s: float
    detect_s: float
    counts: dict[str, dict[str, Counts]]  # method -> type -> counts


def run_day(
    config: SimConfig,
    population: Population,
    reference: ReferenceData,
    seed: int,
    day: int,
    methods: dict[str, Method] | None = None,
    timing_repeats: int = 1,
) -> DayRun:
    events: list[Event] = []
    started = time.perf_counter()
    result = simulate_day(config, seed, day, events.append, population=population)
    sim_s = time.perf_counter() - started

    path = labels_path(seed, day)
    write_labels(path, result.episodes)
    episodes = read_labels(path)

    detect_s = float("inf")
    for _ in range(timing_repeats):  # best-of-N for a steadier throughput number
        started = time.perf_counter()
        rule_alerts = DetectorSuite(reference).run(events)
        detect_s = min(detect_s, time.perf_counter() - started)

    owner_of = population.owner_of
    counts = {"rules": count(rule_alerts, episodes, owner_of)}
    for name, method in (methods or {}).items():
        counts[name] = count(method(events, rule_alerts), episodes, owner_of)
    return DayRun(seed, day, len(events), len(episodes), sim_s, detect_s, counts)


def evaluate(
    days: int,
    config: SimConfig | None = None,
    methods: dict[str, Method] | None = None,
    first_seed: int = EVAL_SEED_BASE,
    progress: Callable[[DayRun], None] | None = None,
    timing_repeats: int = 1,
) -> dict[str, object]:
    config = config or SimConfig()
    population = build_population(config)
    reference = reference_data(config, population)
    totals: dict[str, dict[str, Counts]] = {}
    events = 0
    sim_s = detect_s = 0.0
    for i in range(days):
        run = run_day(config, population, reference, first_seed + i, i, methods, timing_repeats)
        if progress is not None:
            progress(run)
        events += run.events
        sim_s += run.sim_s
        detect_s += run.detect_s
        for method, per_type in run.counts.items():
            bucket = totals.setdefault(method, {})
            for kind, counts in per_type.items():
                bucket.setdefault(kind, Counts()).add(counts)
    return {
        "days": days,
        "seeds": [first_seed, first_seed + days - 1],
        "session_minutes": config.session_minutes,
        "events": events,
        "throughput": {
            "sim_events_per_s": round(events / sim_s),
            "detector_events_per_s": round(events / detect_s),
        },
        "methods": {
            method: {kind: counts.metrics() for kind, counts in per_type.items()}
            for method, per_type in totals.items()
        },
    }

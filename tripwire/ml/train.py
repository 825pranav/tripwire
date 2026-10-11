"""Train the Isolation Forest on simulated *clean* days (no manipulation injected).

    python -m tripwire.ml.train --days 10

Training days use seeds 0..days-1; the score percentiles that set the alert threshold
come from separate held-out clean days. Nothing here reads labels: clean days have none.
"""

from __future__ import annotations

import argparse
import time

import msgspec
import numpy as np
from sklearn.ensemble import IsolationForest

from tripwire.common.events import Event
from tripwire.ml.features import FEATURES, WindowFeatures
from tripwire.ml.model import MODEL_PATH, AnomalyModel
from tripwire.sim.config import CLEAN_DAY, SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data

THRESHOLD_PERCENTILE = 99.5


def clean_windows(config: SimConfig, seeds: range) -> np.ndarray:
    population = build_population(config)
    reference = reference_data(config, population)
    rows = []
    for i, seed in enumerate(seeds):
        events: list[Event] = []
        simulate_day(config, seed, i, events.append, population=population)
        rows.extend(WindowFeatures(reference).run(events))
    return np.array([row.features for row in rows], dtype=np.float64)


def train(days: int, calibration_days: int = 3, trees: int = 200) -> AnomalyModel:
    config = msgspec.structs.replace(SimConfig(), episodes=CLEAN_DAY)
    x_train = clean_windows(config, range(days))
    x_cal = clean_windows(config, range(days, days + calibration_days))
    # Large subsamples so rare-but-normal values (e.g. an accidental same-owner cross)
    # appear in the trees; with the default 256 such features are constant and ignored.
    forest = IsolationForest(
        n_estimators=trees, max_samples=min(4096, len(x_train)), random_state=0, n_jobs=1
    ).fit(x_train)
    model = AnomalyModel(forest, FEATURES, 0.0, 0.0, 0.0)
    raw = model.raw(x_cal)
    model.median = float(np.percentile(raw, 50))
    model.threshold = float(np.percentile(raw, THRESHOLD_PERCENTILE))
    model.top = float(max(raw.max(), model.threshold + 1e-6))
    print(
        f"trained on {len(x_train):,} clean windows ({days} days), calibrated on "
        f"{len(x_cal):,} ({calibration_days} days): threshold = p{THRESHOLD_PERCENTILE} "
        f"= {model.threshold:.4f}"
    )
    return model


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tripwire.ml.train")
    parser.add_argument("--days", type=int, default=10)
    args = parser.parse_args()
    started = time.perf_counter()
    model = train(args.days)
    model.save()
    print(f"saved {MODEL_PATH} in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()

"""Isolation Forest anomaly model with its calibration, saved as one joblib bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from tripwire.ml.features import FEATURES

MODEL_PATH = Path(__file__).resolve().parent / "models" / "iforest.joblib"


@dataclass
class AnomalyModel:
    forest: IsolationForest
    feature_names: tuple[str, ...]
    median: float  # anomaly score percentiles on held-out clean windows
    threshold: float  # alert at or above this (a high percentile of clean windows)
    top: float

    def raw(self, x: np.ndarray) -> np.ndarray:
        """Anomaly score: higher is more unusual."""
        return -self.forest.score_samples(x)

    def normalise(self, raw: np.ndarray) -> np.ndarray:
        """Map raw scores to 0..1 where 0.5 is the alert threshold."""
        low = np.clip((raw - self.median) / (self.threshold - self.median), 0, 1) * 0.5
        high = 0.5 + np.clip((raw - self.threshold) / (self.top - self.threshold), 0, 1) * 0.5
        return np.where(raw < self.threshold, low, high)

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path, compress=3)


def load_model(path: Path = MODEL_PATH) -> AnomalyModel:
    model = joblib.load(path)
    if not isinstance(model, AnomalyModel) or model.feature_names != FEATURES:
        raise ValueError(f"{path} is not a model for the current feature set; run `make train`")
    return model

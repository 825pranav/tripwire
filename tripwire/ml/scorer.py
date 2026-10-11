"""Streaming anomaly scoring: windows close, get scored in a batch, and the ones above the
model's threshold become `ml_anomaly` alerts. Pure: no I/O beyond the model passed in."""

from __future__ import annotations

from collections.abc import Iterable

import msgspec
import numpy as np

from tripwire.common.alerts import Alert
from tripwire.common.events import Event
from tripwire.common.reference import ReferenceData
from tripwire.ml.features import FEATURES, WindowFeatures, WindowRow
from tripwire.ml.model import AnomalyModel


class ScoredWindow(msgspec.Struct, frozen=True):
    row: WindowRow
    score: float  # 0..1, 0.5 = alert threshold


class MLScorer:
    def __init__(self, model: AnomalyModel, reference: ReferenceData, window_s: int = 300) -> None:
        self.model = model
        self.features = WindowFeatures(reference, window_s)
        self.scored: list[ScoredWindow] = []  # every window scored so far (for fusion)

    def process(self, event: Event) -> list[Alert]:
        rows = self.features.process(event)
        return self._score(rows, event.ts) if rows else []

    def finish(self, ts: int) -> list[Alert]:
        return self._score(self.features.finish(), ts)

    def run(self, events: Iterable[Event]) -> list[Alert]:
        alerts: list[Alert] = []
        last = 0
        for event in events:
            alerts.extend(self.process(event))
            last = event.ts
        alerts.extend(self.finish(last))
        return alerts

    def _score(self, rows: list[WindowRow], now: int) -> list[Alert]:
        if not rows:
            return []
        x = np.array([row.features for row in rows], dtype=np.float64)
        scores = self.model.normalise(self.model.raw(x))
        alerts = []
        for row, score in zip(rows, scores, strict=True):
            self.scored.append(ScoredWindow(row, float(score)))
            if score >= 0.5:
                alerts.append(window_alert(row, float(score), now))
        return alerts


def window_alert(
    row: WindowRow, score: float, now: int, kind: str = "ml_anomaly", source: str = "ml"
) -> Alert:
    return Alert(
        alert_id=f"{kind}-{row.symbol}-{row.account}-{row.start_ns}",
        type=kind,
        symbol=row.symbol,
        accounts=(row.account,),
        window_start_ns=row.start_ns,
        window_end_ns=row.end_ns,
        order_ids=row.order_ids,
        score=round(score, 4),
        features={
            name: round(value, 4) for name, value in zip(FEATURES, row.features, strict=True)
        },
        emitted_ts=now,
        source=source,
    )

"""Combining rule alerts with ML window scores.

- union:  every rule alert plus every ML alert.
- fusion: per (account, symbol, window), a noisy-OR of the two scores,
  fused = 1 - (1 - rule) * (1 - ml), where `rule` is the strongest rule alert for that
  account and symbol overlapping the window (0 if none). Windows with fused >=
  `threshold` are flagged, typed by the rule alert when there is one, otherwise
  `ml_anomaly`. The threshold sits above the ML-only alert level (0.5), so a lone
  borderline ML score is dropped while strong ML scores and rule hits survive.
"""

from __future__ import annotations

from collections import defaultdict

from tripwire.common.alerts import Alert
from tripwire.ml.scorer import ScoredWindow, window_alert


def union(rule_alerts: list[Alert], ml_alerts: list[Alert]) -> list[Alert]:
    return [*rule_alerts, *ml_alerts]


def fuse(
    rule_alerts: list[Alert],
    windows: list[ScoredWindow],
    threshold: float = 0.6,
) -> list[Alert]:
    by_key: dict[tuple[str, str], list[Alert]] = defaultdict(list)
    for alert in rule_alerts:
        for account in alert.accounts:
            by_key[(account, alert.symbol)].append(alert)

    fused_alerts = []
    for window in windows:
        row = window.row
        overlapping = [
            a
            for a in by_key.get((row.account, row.symbol), [])
            if a.window_start_ns < row.end_ns and a.window_end_ns >= row.start_ns
        ]
        best = max(overlapping, key=lambda a: a.score, default=None)
        rule_score = best.score if best else 0.0
        fused = 1 - (1 - rule_score) * (1 - window.score)
        if fused >= threshold:
            kind = best.type if best else "ml_anomaly"
            fused_alerts.append(window_alert(row, fused, row.end_ns, kind, source="fusion"))
    return fused_alerts

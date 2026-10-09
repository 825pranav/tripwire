"""Matching alerts to labelled episodes.

An alert matches an episode when it is on the same symbol, shares at least one account
with it (or the same beneficial owner), and its time window overlaps the episode's
window, allowing `TOLERANCE_NS` of slack. Rule alerts must also have the episode's type;
ML alerts (`ml_anomaly`) are untyped and may match any episode.
"""

from __future__ import annotations

from tripwire.common.alerts import Alert
from tripwire.sim.labels import Episode

TOLERANCE_NS = 30 * 1_000_000_000
EPISODE_TYPES = ("spoofing", "layering", "wash", "marking_close")
UNTYPED = "ml_anomaly"


def matches(alert: Alert, episode: Episode, owner_of: dict[str, str]) -> bool:
    if alert.type != UNTYPED and alert.type != episode.type:
        return False
    if alert.symbol != episode.symbol:
        return False
    if alert.window_end_ns + TOLERANCE_NS < episode.start_ns:
        return False
    if alert.window_start_ns - TOLERANCE_NS > episode.end_ns:
        return False
    if set(alert.accounts) & set(episode.accounts):
        return True
    return any(owner_of.get(a) == episode.owner for a in alert.accounts)

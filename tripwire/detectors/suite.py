"""Runs every rule detector over one event stream with shared market state.

Used unchanged by the Kafka runner (one event at a time) and the eval harness (a whole
simulated day in-process).
"""

from __future__ import annotations

from collections.abc import Iterable

from tripwire.common.alerts import Alert
from tripwire.common.events import Event
from tripwire.common.reference import ReferenceData
from tripwire.detectors.base import Detector
from tripwire.detectors.config import DetectorConfig, load_config
from tripwire.detectors.layering import LayeringDetector
from tripwire.detectors.marking_close import MarkingCloseDetector
from tripwire.detectors.spoofing import SpoofingDetector
from tripwire.detectors.state import MarketState
from tripwire.detectors.wash import WashDetector


class DetectorSuite:
    def __init__(self, reference: ReferenceData, config: DetectorConfig | None = None) -> None:
        config = config or load_config()
        self.state = MarketState()
        self.detectors: list[Detector] = [
            SpoofingDetector(config.spoofing),
            LayeringDetector(config.layering),
            WashDetector(config.wash, reference.owner_of()),
            MarkingCloseDetector(config.marking_close, reference.session),
        ]
        self.last_ts = 0

    def process(self, event: Event) -> list[Alert]:
        alerts: list[Alert] = []
        state = self.state
        for detector in self.detectors:
            found = detector.on_event(event, state)
            if found:
                alerts.extend(found)
        state.apply(event)
        self.last_ts = event.ts
        return alerts

    def finish(self) -> list[Alert]:
        alerts: list[Alert] = []
        for detector in self.detectors:
            alerts.extend(detector.finish(self.last_ts))
        return alerts

    def run(self, events: Iterable[Event]) -> list[Alert]:
        alerts: list[Alert] = []
        for event in events:
            found = self.process(event)
            if found:
                alerts.extend(found)
        alerts.extend(self.finish())
        return alerts

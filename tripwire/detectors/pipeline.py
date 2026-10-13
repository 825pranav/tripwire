"""Kafka messages in, alerts out: decode, restore per-symbol order, run the rule suite and
the ML scorer. Shared by the detector service and the end-to-end benchmark."""

from __future__ import annotations

from tripwire.common.alerts import Alert
from tripwire.common.codec import decode
from tripwire.common.events import Event
from tripwire.common.kafka import Headers, SymbolReorderer, header
from tripwire.common.reference import ReferenceData
from tripwire.detectors.suite import DetectorSuite
from tripwire.ml.model import AnomalyModel
from tripwire.ml.scorer import MLScorer


class DetectionPipeline:
    def __init__(self, reference: ReferenceData, model: AnomalyModel | None) -> None:
        self.suite = DetectorSuite(reference)
        self.scorer = MLScorer(model, reference) if model is not None else None
        self.reorderer = SymbolReorderer()

    def handle(self, value: bytes | None, headers: Headers) -> tuple[list[Event], list[Alert]]:
        """Process one message. Returns the events released in order, and any alerts."""
        run = header(headers, "run")
        seq = header(headers, "seq")
        if value is None or run is None or seq is None:
            return [], []
        event = decode(value)
        released = self.reorderer.push(event.symbol, run, int(seq), event)
        alerts: list[Alert] = []
        for ready in released:
            found = self.suite.process(ready)
            if found:
                alerts.extend(found)
            if self.scorer is not None:
                found = self.scorer.process(ready)
                if found:
                    alerts.extend(found)
        return released, alerts

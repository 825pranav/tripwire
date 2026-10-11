"""Detection methods compared by the eval, besides the rules themselves."""

from __future__ import annotations

from collections.abc import Sequence

from tripwire.common.alerts import Alert
from tripwire.common.events import Event
from tripwire.common.reference import ReferenceData
from tripwire.eval.harness import Method
from tripwire.ml.fusion import fuse, union
from tripwire.ml.model import AnomalyModel, load_model
from tripwire.ml.scorer import MLScorer, ScoredWindow


def ml_methods(reference: ReferenceData, model: AnomalyModel | None = None) -> dict[str, Method]:
    """`ml`, `union` and `fusion`. The ML pass runs once per day and is shared."""
    model = model or load_model()
    last: dict[int, tuple[list[Alert], list[ScoredWindow]]] = {}

    def ml_pass(events: Sequence[Event]) -> tuple[list[Alert], list[ScoredWindow]]:
        if id(events) not in last:
            last.clear()
            scorer = MLScorer(model, reference)
            last[id(events)] = (scorer.run(events), scorer.scored)
        return last[id(events)]

    def ml(events: Sequence[Event], rules: list[Alert]) -> list[Alert]:
        return ml_pass(events)[0]

    def combined_union(events: Sequence[Event], rules: list[Alert]) -> list[Alert]:
        return union(rules, ml_pass(events)[0])

    def fusion(events: Sequence[Event], rules: list[Alert]) -> list[Alert]:
        return fuse(rules, ml_pass(events)[1])

    return {"ml": ml, "union": combined_union, "fusion": fusion}

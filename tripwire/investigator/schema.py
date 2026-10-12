"""Case report schema. The JSON schema generated from these structs is passed to Ollama's
`format` parameter, so the model can only answer in this shape."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import msgspec

EventKind = Literal["accepted", "cancelled", "trade"]
Confidence = Literal["low", "medium", "high"]
Action = Literal["close_no_action", "monitor", "escalate_to_compliance", "refer_to_regulator"]


class EvidenceItem(msgspec.Struct, frozen=True):
    order_id: int
    event: EventKind
    t_ms: int  # milliseconds after the evidence window start
    side: Literal["B", "S"]
    qty: int
    price: int | None  # ticks; null for market orders
    note: Annotated[str, msgspec.Meta(max_length=200)]


class CaseReport(msgspec.Struct, frozen=True):
    summary: Annotated[str, msgspec.Meta(max_length=600)]
    behavior_narrative: Annotated[str, msgspec.Meta(max_length=2000)]
    evidence: Annotated[list[EvidenceItem], msgspec.Meta(min_length=1, max_length=12)]
    confidence: Confidence
    recommended_action: Action


def report_json_schema() -> dict[str, Any]:
    """Inline JSON schema (no $ref), which local models follow more reliably."""
    schema = msgspec.json.schema(CaseReport)
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved

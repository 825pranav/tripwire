"""Citation validator: every order the report cites must exist in the evidence window,
and the attributes the report states (event, time, side, size, price) must match the
stored event exactly (time within `TIME_TOLERANCE_MS`).

It also catches one prose error small models make: writing a tick price as dollars
("$9927" for 9927 ticks, i.e. $99.27). That counts as an error, so it triggers the retry,
but not as a citation."""

from __future__ import annotations

import re

import msgspec

from tripwire.investigator.evidence import EvidenceWindow
from tripwire.investigator.schema import CaseReport

TIME_TOLERANCE_MS = 1
DOLLAR_AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


def dollars(ticks: int) -> str:
    return f"{ticks / 100:.2f}"


class Validation(msgspec.Struct, frozen=True):
    total: int
    valid: int
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors and self.total > 0


def validate(report: CaseReport, window: EvidenceWindow) -> Validation:
    by_order: dict[int, list[tuple[str, int, str, int, int | None]]] = {}
    for row in window.rows:
        by_order.setdefault(row.order_id, []).append(
            (row.event, row.t_ms, row.side, row.qty, row.price)
        )
    errors: list[str] = []
    valid = 0
    for i, item in enumerate(report.evidence):
        prefix = f"evidence[{i}] order {item.order_id}"
        stored = by_order.get(item.order_id)
        if stored is None:
            errors.append(f"{prefix}: no such order in the evidence window")
            continue
        same_kind = [s for s in stored if s[0] == item.event]
        if not same_kind:
            kinds = sorted({s[0] for s in stored})
            errors.append(f"{prefix}: no '{item.event}' event (window has {kinds})")
            continue
        problems = _best_match_problems(item.t_ms, item.side, item.qty, item.price, same_kind)
        if problems:
            errors.append(f"{prefix} ({item.event}): " + "; ".join(problems))
        else:
            valid += 1
    if not report.evidence:
        errors.append("report cites no evidence")
    errors.extend(_ticks_written_as_dollars(report, window))
    return Validation(len(report.evidence), valid, errors)


def _ticks_written_as_dollars(report: CaseReport, window: EvidenceWindow) -> list[str]:
    ticks = {row.price for row in window.rows if row.price is not None}
    errors = []
    for field in ("summary", "behavior_narrative"):
        for match in DOLLAR_AMOUNT.finditer(getattr(report, field)):
            amount = match.group(1).replace(",", "")
            if "." not in amount and int(amount) in ticks:
                errors.append(
                    f"{field}: ${amount} is a tick price; write it in dollars "
                    f"(${dollars(int(amount))})"
                )
    return errors


def _best_match_problems(
    t_ms: int,
    side: str,
    qty: int,
    price: int | None,
    candidates: list[tuple[str, int, str, int, int | None]],
) -> list[str]:
    """Problems against the closest stored event of that kind (none if one matches)."""
    best: list[str] | None = None
    for _, s_t, s_side, s_qty, s_price in candidates:
        problems = []
        if abs(s_t - t_ms) > TIME_TOLERANCE_MS:
            problems.append(f"t_ms {t_ms} != stored {s_t}")
        if side != s_side:
            problems.append(f"side {side} != stored {s_side}")
        if qty != s_qty:
            problems.append(f"qty {qty} != stored {s_qty}")
        if price != s_price:
            problems.append(f"price {price} != stored {s_price}")
        if best is None or len(problems) < len(best):
            best = problems
    return best or []

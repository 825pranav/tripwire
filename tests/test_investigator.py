from typing import Any

import msgspec
import pytest
from scenario import BUY, SELL, Scenario

from tripwire.common.alerts import Alert
from tripwire.investigator.agent import LLMUnavailable, Message, investigate, template_report
from tripwire.investigator.evidence import EvidenceWindow, build_window
from tripwire.investigator.regulations import regulations_for
from tripwire.investigator.schema import CaseReport, EvidenceItem, report_json_schema
from tripwire.investigator.validator import validate


@pytest.fixture(scope="module")
def window() -> EvidenceWindow:
    s = Scenario()
    s.background_book()
    big = s.limit("SPF", BUY, 9_999, 5_000)
    s.wait(5)
    s.limit("SPF", SELL, 10_000, 200)
    s.wait(300)
    s.market("NOISE", BUY, 200)
    s.wait(10)
    s.cancel(big)
    [alert] = s.alerts()
    return build_window(alert, s.events)


def item_from_row(window: EvidenceWindow, index: int, **changes: Any) -> EvidenceItem:
    r = window.rows[index]
    item = EvidenceItem(r.order_id, r.event, r.t_ms, r.side, r.qty, r.price, "x")  # type: ignore[arg-type]
    return msgspec.structs.replace(item, **changes)


def report(*items: EvidenceItem) -> CaseReport:
    return CaseReport("s", "n", list(items), "high", "escalate_to_compliance")


def test_window_contains_only_alerted_accounts(window: EvidenceWindow) -> None:
    assert {r.account for r in window.rows} == {"SPF"}
    assert {r.event for r in window.rows} == {"accepted", "cancelled", "trade"}


def test_validator_accepts_exact_citations(window: EvidenceWindow) -> None:
    items = [item_from_row(window, i) for i in range(len(window.rows))]
    check = validate(report(*items), window)
    assert check.ok and check.valid == check.total == len(window.rows)


def test_validator_rejects_fabricated_order_id(window: EvidenceWindow) -> None:
    check = validate(report(item_from_row(window, 0, order_id=987_654)), window)
    assert not check.ok
    assert "no such order" in check.errors[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [("qty", 12_345), ("price", 1), ("side", "S"), ("t_ms", 999_999), ("event", "trade")],
)
def test_validator_rejects_mismatched_attributes(
    window: EvidenceWindow, field: str, value: object
) -> None:
    accepted = next(
        i for i, r in enumerate(window.rows) if r.event == "accepted" and r.qty == 5_000
    )
    check = validate(report(item_from_row(window, accepted, **{field: value})), window)
    assert not check.ok and check.valid == 0


def test_template_report_is_fully_valid(window: EvidenceWindow) -> None:
    check = validate(template_report(window), window)
    assert check.ok


class FakeLLM:
    model = "fake"

    def __init__(self, *outputs: str | Exception) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[Message]] = []

    def generate(self, messages: list[Message], schema: dict[str, Any]) -> str:
        self.calls.append(messages)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def encoded(r: CaseReport) -> str:
    return msgspec.json.encode(r).decode()


def test_verified_on_first_try(window: EvidenceWindow) -> None:
    llm = FakeLLM(encoded(report(item_from_row(window, 0))))
    result = investigate(window, llm)
    assert (result.status, result.attempts) == ("verified", 1)
    assert result.regulations == regulations_for("spoofing")


def test_retry_feeds_back_errors_then_verifies(window: EvidenceWindow) -> None:
    bad = encoded(report(item_from_row(window, 0, qty=1)))
    good = encoded(report(item_from_row(window, 0)))
    llm = FakeLLM(bad, good)
    result = investigate(window, llm)
    assert (result.status, result.attempts) == ("verified", 2)
    assert "failed citation validation" in llm.calls[1][-1]["content"]
    assert "qty 1 != stored" in llm.calls[1][-1]["content"]


def test_unverified_after_failed_retry(window: EvidenceWindow) -> None:
    bad = encoded(report(item_from_row(window, 0, order_id=1)))
    result = investigate(window, FakeLLM(bad, bad))
    assert (result.status, result.attempts) == ("unverified", 2)
    assert result.validation.errors


def test_fallback_when_llm_unreachable(window: EvidenceWindow) -> None:
    result = investigate(window, FakeLLM(LLMUnavailable("connection refused")))
    assert result.status == "fallback" and result.model == "template"
    assert result.validation.ok


def test_regulations_cover_every_alert_type_and_are_static() -> None:
    for kind in ("spoofing", "layering", "wash", "marking_close", "ml_anomaly"):
        assert regulations_for(kind)
    assert any("9(a)(1)" in r.citation for r in regulations_for("wash"))


def test_report_schema_is_inline_for_ollama() -> None:
    schema = report_json_schema()
    assert "$ref" not in str(schema) and "$defs" not in schema
    assert set(schema["required"]) == {
        "summary",
        "behavior_narrative",
        "evidence",
        "confidence",
        "recommended_action",
    }


def test_alert_window_padding(window: EvidenceWindow) -> None:
    alert: Alert = window.alert
    assert window.start_ns == alert.window_start_ns - 30_000_000_000
    assert all(r.t_ms >= 0 for r in window.rows)


def test_validator_rejects_tick_prices_written_as_dollars(window: EvidenceWindow) -> None:
    item = item_from_row(window, 0)
    assert item.price is not None
    wrong = CaseReport(f"sold at ${item.price}", "n", [item], "high", "monitor")
    check = validate(wrong, window)
    assert not check.ok and check.valid == 1
    assert f"${item.price / 100:.2f}" in check.errors[0]
    right = CaseReport(f"sold at ${item.price / 100:.2f}", "n", [item], "high", "monitor")
    assert validate(right, window).ok

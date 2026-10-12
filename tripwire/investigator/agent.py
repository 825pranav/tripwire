"""Write a case report for one alert: ask the local LLM for a structured report grounded in
the evidence window, validate every citation, retry once with the validation errors, and
fall back to a deterministic template if the LLM cannot be reached.

Status of the stored report:
- verified:   every cited order exists and its stated attributes match the stored event
- unverified: the LLM still had citation errors after the retry (stored, but flagged)
- fallback:   the LLM was unavailable; a template report built straight from the evidence
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx
import msgspec
import ollama

from tripwire.investigator.evidence import EvidenceRow, EvidenceWindow
from tripwire.investigator.regulations import DISCLAIMER, Regulation, regulations_for
from tripwire.investigator.schema import CaseReport, EvidenceItem, report_json_schema
from tripwire.investigator.validator import Validation, validate

Message = dict[str, str]

SYSTEM_PROMPT = """You are a trade surveillance analyst at an exchange. Write a short case \
report about one alert.

Rules:
- Use ONLY the evidence table. Every evidence entry must copy order_id, event, t_ms, side, \
qty and price exactly from one table row. Never cite an order that is not in the table.
- Prices are integer ticks (1 tick = $0.01). t_ms is milliseconds after the window start.
- Describe what the account did and why it matches (or does not match) the alert type. \
Mention legitimate explanations if the evidence allows them.
- Do not cite laws or regulations; they are attached separately.
- Answer with JSON matching the given schema."""

RULE_HINTS = {
    "spoofing": "large order near the touch cancelled within milliseconds of the same "
    "account trading on the other side",
    "layering": "several orders at stepped prices on one side cancelled together right "
    "after the same account traded on the other side",
    "wash": "accounts with the same beneficial owner repeatedly trading with each other",
    "marking_close": "one-directional aggressive trading in the final minutes of the "
    "session, moving the closing price",
    "ml_anomaly": "statistically unusual behaviour for this account and symbol compared "
    "with normal trading days",
}


class LLM(Protocol):
    model: str

    def generate(self, messages: list[Message], schema: dict[str, Any]) -> str: ...


class LLMUnavailable(Exception):
    pass


class OllamaLLM:
    def __init__(self, host: str, model: str, timeout_s: float = 300) -> None:
        self.model = model
        self.client = ollama.Client(host=host, timeout=timeout_s)

    def generate(self, messages: list[Message], schema: dict[str, Any]) -> str:
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                format=schema,
                options={"temperature": 0, "num_ctx": 8192},
            )
        except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as exc:
            raise LLMUnavailable(str(exc)) from exc
        except ollama.ResponseError as exc:
            raise LLMUnavailable(f"ollama error {exc.status_code}: {exc.error}") from exc
        return response.message.content or ""

    def available(self) -> bool:
        try:
            names = {m.model for m in self.client.list().models}
        except Exception:
            return False
        return self.model in names or f"{self.model}:latest" in names


class CaseResult(msgspec.Struct, frozen=True):
    status: str  # verified | unverified | fallback
    model: str
    report: CaseReport
    regulations: list[Regulation]
    disclaimer: str
    attempts: int
    validation: Validation
    latency_ms: int


def evidence_table(rows: list[EvidenceRow]) -> str:
    lines = ["order_id | event | t_ms | side | qty | price | account | detail"]
    for r in rows:
        price = "null" if r.price is None else str(r.price)
        lines.append(
            f"{r.order_id} | {r.event} | {r.t_ms} | {r.side} | {r.qty} | {price} | "
            f"{r.account} | {r.detail}"
        )
    return "\n".join(lines)


def build_messages(window: EvidenceWindow) -> list[Message]:
    alert = window.alert
    features = ", ".join(f"{k}={v}" for k, v in alert.features.items())
    user = (
        f"Alert {alert.alert_id}\n"
        f"type: {alert.type} ({RULE_HINTS.get(alert.type, '')})\n"
        f"symbol: {alert.symbol}\naccounts: {', '.join(alert.accounts)}\n"
        f"score: {alert.score}\ndetector features: {features}\n"
        f"orders that triggered the alert: {', '.join(map(str, alert.order_ids[:20]))}\n\n"
        f"Evidence table ({len(window.rows)} rows):\n{evidence_table(window.rows)}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def template_report(window: EvidenceWindow) -> CaseReport:
    """Deterministic report built straight from the evidence (used when the LLM is down)."""
    alert = window.alert
    alerted = set(alert.order_ids)
    rows = [r for r in window.rows if r.order_id in alerted] or window.rows
    evidence = [
        EvidenceItem(r.order_id, r.event, r.t_ms, r.side, r.qty, r.price, r.detail)  # type: ignore[arg-type]
        for r in rows[:12]
    ]
    features = "; ".join(f"{k} = {v}" for k, v in alert.features.items())
    return CaseReport(
        summary=(
            f"{alert.type} alert on {alert.symbol} for {', '.join(alert.accounts)} "
            f"(score {alert.score:.2f}). Template report: the LLM was unavailable."
        ),
        behavior_narrative=(
            f"The detector flagged: {RULE_HINTS.get(alert.type, alert.type)}. "
            f"Measured features: {features}. The cited orders are those that triggered it."
        ),
        evidence=evidence or [EvidenceItem(0, "accepted", 0, "B", 0, None, "no evidence rows")],
        confidence="medium" if alert.score >= 0.6 else "low",
        recommended_action="escalate_to_compliance" if alert.score >= 0.6 else "monitor",
    )


def investigate(window: EvidenceWindow, llm: LLM | None) -> CaseResult:
    started = time.perf_counter()
    regulations = regulations_for(window.alert.type)

    def result(status: str, report: CaseReport, attempts: int, check: Validation) -> CaseResult:
        latency = int((time.perf_counter() - started) * 1000)
        model = llm.model if llm is not None and status != "fallback" else "template"
        return CaseResult(status, model, report, regulations, DISCLAIMER, attempts, check, latency)

    if llm is not None:
        messages = build_messages(window)
        schema = report_json_schema()
        try:
            report, check = _ask(llm, messages, schema, window)
            if check.ok:
                return result("verified", report, 1, check)
            messages = [
                *messages,
                {"role": "assistant", "content": msgspec.json.encode(report).decode()},
                {
                    "role": "user",
                    "content": "Your report failed citation validation:\n- "
                    + "\n- ".join(check.errors)
                    + "\nReturn the full report again with every evidence entry copied "
                    "exactly from one row of the evidence table.",
                },
            ]
            report, check = _ask(llm, messages, schema, window)
            return result("verified" if check.ok else "unverified", report, 2, check)
        except LLMUnavailable:
            pass
    report = template_report(window)
    return result("fallback", report, 0, validate(report, window))


def _ask(
    llm: LLM, messages: list[Message], schema: dict[str, Any], window: EvidenceWindow
) -> tuple[CaseReport, Validation]:
    raw = llm.generate(messages, schema)
    try:
        report = msgspec.json.decode(raw, type=CaseReport)
    except msgspec.ValidationError as exc:
        # Schema-constrained output should not fail to parse; treat it as zero valid citations.
        empty = CaseReport(f"unparseable model output: {exc}", raw[:2000], [], "low", "monitor")
        return empty, Validation(0, 0, [f"output did not match the schema: {exc}"])
    return report, validate(report, window)

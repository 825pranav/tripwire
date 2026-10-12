"""Offline citation accuracy: investigate a sample of alerts from a simulated eval day
in-process (no Kafka/Postgres) and measure how many cited orders check out."""

from __future__ import annotations

import random
import sys
from typing import Any

from tripwire.common.alerts import Alert
from tripwire.common.config import load_settings
from tripwire.common.events import Event
from tripwire.detectors.suite import DetectorSuite
from tripwire.investigator.agent import CaseResult, OllamaLLM, investigate
from tripwire.investigator.evidence import build_window
from tripwire.ml.model import load_model
from tripwire.ml.scorer import MLScorer
from tripwire.sim.config import SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data

SAMPLE_SEED = 2000  # a day not used by the detector eval


def sample_alerts(n: int, seed: int = SAMPLE_SEED) -> tuple[list[Alert], list[Event]]:
    config = SimConfig()
    population = build_population(config)
    reference = reference_data(config, population)
    events: list[Event] = []
    simulate_day(config, seed, 0, events.append, population=population)
    alerts = DetectorSuite(reference).run(events) + MLScorer(load_model(), reference).run(events)
    sample = random.Random(seed).sample(alerts, min(n, len(alerts)))
    return sample, events


def citation_accuracy(n: int) -> dict[str, Any]:
    settings = load_settings()
    llm = OllamaLLM(settings.ollama_host, settings.ollama_model)
    if not llm.available():
        return {"skipped": f"ollama model {settings.ollama_model} not reachable"}
    alerts, events = sample_alerts(n)
    results: list[CaseResult] = []
    for i, alert in enumerate(alerts):
        result = investigate(build_window(alert, events), llm)
        results.append(result)
        print(
            f"  citation eval {i + 1}/{len(alerts)}: {alert.type} -> {result.status} "
            f"({result.validation.valid}/{result.validation.total} valid, {result.latency_ms} ms)",
            file=sys.stderr,
        )
    first_try = sum(r.status == "verified" and r.attempts == 1 for r in results)
    total = sum(r.validation.total for r in results)
    valid = sum(r.validation.valid for r in results)
    return {
        "model": settings.ollama_model,
        "reports": len(results),
        "citations": total,
        "valid_citations": valid,
        "accuracy": round(valid / total, 4) if total else None,
        "verified_reports": sum(r.status == "verified" for r in results),
        "verified_first_try": first_try,
        "unverified_reports": sum(r.status == "unverified" for r in results),
        "fallback_reports": sum(r.status == "fallback" for r in results),
        "median_latency_ms": sorted(r.latency_ms for r in results)[len(results) // 2]
        if results
        else None,
    }


if __name__ == "__main__":
    print(citation_accuracy(int(sys.argv[1]) if len(sys.argv) > 1 else 5))

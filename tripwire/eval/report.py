"""Turn eval results into a markdown table, compare runs, and check against the baseline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tripwire.eval.matching import EPISODE_TYPES

RESULTS_DIR = Path(__file__).resolve().parent / "results"
BASELINE_PATH = RESULTS_DIR / "baseline.json"
PROPOSED_BASELINE_PATH = RESULTS_DIR / "baseline.proposed.json"
F1_TOLERANCE = 0.05
THROUGHPUT_TOLERANCE = 0.20


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def markdown(results: dict[str, Any]) -> str:
    lines = [
        f"Eval over {results['days']} simulated days (seeds {results['seeds'][0]}-"
        f"{results['seeds'][1]}, {results['session_minutes']}-minute sessions, "
        f"{results['events']:,} events).",
        "",
        "| method | type | precision | recall | F1 | alerts | episodes detected |",
        "|---|---|---|---|---|---|---|",
    ]
    for method, per_type in results["methods"].items():
        for kind in (*EPISODE_TYPES, "overall"):
            m = per_type[kind]
            lines.append(
                f"| {method} | {kind} | {_fmt(m['precision'])} | {_fmt(m['recall'])} | "
                f"{_fmt(m['f1'])} | {m['alerts']} | {m['detected']}/{m['episodes']} |"
            )
    tp = results["throughput"]
    lines += [
        "",
        f"Throughput (in-process, single thread): simulator {tp['sim_events_per_s']:,} events/s, "
        f"detectors {tp['detector_events_per_s']:,} events/s.",
    ]
    c = results.get("citations")
    if c and "skipped" in c:
        lines.append(f"LLM citation check skipped: {c['skipped']}.")
    elif c:
        lines.append(
            f"LLM citation accuracy ({c['model']}, {c['reports']} sampled alerts): "
            f"{c['valid_citations']}/{c['citations']} cited orders valid ({c['accuracy']}); "
            f"{c['verified_reports']} reports verified ({c['verified_first_try']} on the first "
            f"try), {c['unverified_reports']} unverified, {c['fallback_reports']} fallback; "
            f"median {c['median_latency_ms']} ms per report."
        )
    return "\n".join(lines) + "\n"


def save(results: dict[str, Any], name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / f"{name}.md").write_text(markdown(results), encoding="utf-8")
    return path


def f1_by_key(results: dict[str, Any]) -> dict[str, float | None]:
    return {
        f"{method}/{kind}": m["f1"]
        for method, per_type in results["methods"].items()
        for kind, m in per_type.items()
    }


def check_against_baseline(results: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Problems found: per-type F1 dropping more than 0.05, or detector throughput
    dropping more than 20%. Metrics the baseline doesn't have are not checked."""
    problems = []
    now, before = f1_by_key(results), f1_by_key(baseline)
    for key, old in before.items():
        new = now.get(key)
        if old is not None and new is not None and new < old - F1_TOLERANCE:
            problems.append(f"F1 {key}: {old:.3f} -> {new:.3f}")
    old_tp = baseline["throughput"]["detector_events_per_s"]
    new_tp = results["throughput"]["detector_events_per_s"]
    if new_tp < old_tp * (1 - THROUGHPUT_TOLERANCE):
        problems.append(f"detector throughput: {old_tp:,} -> {new_tp:,} events/s")
    return problems


def diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    """Short text diff of two eval runs (F1 per method/type and throughput)."""
    lines = []
    before, after = f1_by_key(old), f1_by_key(new)
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if a != b:
            lines.append(f"  F1 {key}: {_fmt(a)} -> {_fmt(b)}")
    for key in sorted(set(old.get("throughput", {})) | set(new.get("throughput", {}))):
        a, b = old.get("throughput", {}).get(key), new.get("throughput", {}).get(key)
        if a != b:
            lines.append(f"  {key}: {a} -> {b}")
    return "\n".join(lines) if lines else "  (no changes)"

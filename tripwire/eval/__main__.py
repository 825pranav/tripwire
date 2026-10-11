"""Eval CLI.

python -m tripwire.eval --days 10            # make eval
python -m tripwire.eval --smoke              # 1 day vs baseline (make check)
python -m tripwire.eval --smoke --save-baseline
python -m tripwire.eval --days 20 --full     # make check-full: saves and diffs vs last
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from tripwire.eval.harness import DayRun, evaluate
from tripwire.eval.methods import ml_methods
from tripwire.eval.report import (
    BASELINE_PATH,
    RESULTS_DIR,
    check_against_baseline,
    diff,
    markdown,
    save,
)
from tripwire.sim.config import SimConfig
from tripwire.sim.population import build_population, reference_data


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tripwire.eval")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--smoke", action="store_true", help="1 day, compare with baseline")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--full", action="store_true", help="save as full_run and diff")
    parser.add_argument("--rules-only", action="store_true", help="skip ML, union and fusion")
    args = parser.parse_args()

    days = 1 if args.smoke else args.days

    def progress(run: DayRun) -> None:
        matched = run.counts["rules"]["overall"].tp_alerts
        print(
            f"  day {run.day:3d} seed {run.seed}: {run.events:,} events, {run.episodes} episodes, "
            f"{matched} matched rule alerts",
            file=sys.stderr,
        )

    config = SimConfig()
    methods = None
    if not args.rules_only:
        reference = reference_data(config, build_population(config))
        methods = ml_methods(reference)
    repeats = 3 if args.smoke else 1
    results = evaluate(days, config, methods, progress=progress, timing_repeats=repeats)

    if args.smoke:
        print(markdown(results))
        if args.save_baseline:
            BASELINE_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(f"baseline saved to {BASELINE_PATH}")
            return 0
        if not BASELINE_PATH.exists():
            print("no baseline yet: run with --save-baseline")
            return 1
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        problems = check_against_baseline(results, baseline)
        if problems:
            print("SMOKE EVAL REGRESSION vs baseline:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("smoke eval: OK vs baseline")
        return 0

    name = "full_run" if args.full else f"eval_{days}days"
    previous = RESULTS_DIR / f"{name}.json"
    old = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else None
    if old is not None:
        shutil.copy(previous, RESULTS_DIR / f"{name}.prev.json")
    path = save(results, name)
    print(markdown(results))
    print(f"saved {path}")
    if old is not None:
        print("diff vs previous run:")
        print(diff(old, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

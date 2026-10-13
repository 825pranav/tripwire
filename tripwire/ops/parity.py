"""`make parity`: check the live pipeline against the in-process one.

Re-simulates a day the realtime simulator streamed (same seed and day number), runs the
rule suite and the ML scorer over it in-process, and compares their alerts with the
alerts the detector service stored for that day. Both paths share all detection code,
so a difference means the streaming layer (Kafka ordering, restarts, state carried from
one day to the next) changed the result. Only meaningful for a day the services saw from
its first event; by default the last day the simulator completed.

    python -m tripwire.ops.parity [--day N] [--save]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import psycopg

from tripwire.common.config import load_settings
from tripwire.common.events import Event, OrderAccepted
from tripwire.detectors.suite import DetectorSuite
from tripwire.ml.model import load_model
from tripwire.ml.scorer import MLScorer
from tripwire.sim.config import SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.population import build_population, reference_data

RESULT_PATH = Path(__file__).resolve().parents[1] / "eval" / "results" / "live_parity.json"


def compare(conn: psycopg.Connection, sim_seed: int, day: int) -> dict[str, Any]:
    config = SimConfig()
    population = build_population(config)
    reference = reference_data(config, population)
    events: list[Event] = []
    simulate_day(config, sim_seed * 100_000 + day, day, events.append, population=population)
    rules = {a.alert_id for a in DetectorSuite(reference).run(events)}
    ml = {a.alert_id for a in MLScorer(load_model(), reference).run(events)}
    start, end = events[0].ts, events[-1].ts
    stored = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT alert_id, source FROM alerts WHERE window_start_ns BETWEEN %s AND %s",
            (start, end),
        )
    }
    row = conn.execute(
        "SELECT count(*) FROM orders WHERE ts_ns BETWEEN %s AND %s", (start, end)
    ).fetchone()
    live_rules = {a for a, source in stored.items() if source == "rules"}
    live_ml = {a for a, source in stored.items() if source == "ml"}
    return {
        "sim_seed": sim_seed,
        "day": day,
        "events": len(events),
        "orders_in_process": sum(isinstance(e, OrderAccepted) for e in events),
        "orders_stored": row[0] if row else 0,
        "rules": {"in_process": len(rules), "live": len(live_rules), "same": rules == live_rules},
        "ml": {"in_process": len(ml), "live": len(live_ml), "same": ml == live_ml},
        "only_in_process": sorted((rules | ml) - set(stored)),
        "only_live": sorted(set(stored) - (rules | ml)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--day", type=int, help="simulated day (default: last completed)")
    parser.add_argument("--save", action="store_true", help=f"write {RESULT_PATH.name}")
    args = parser.parse_args()
    settings = load_settings()
    with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5) as conn:
        day = args.day
        if day is None:
            row = conn.execute(
                "SELECT (details->>'day')::int FROM service_health WHERE service = 'sim'"
            ).fetchone()
            if row is None or row[0] is None or row[0] < 1:
                print("no completed simulated day yet; pass --day", file=sys.stderr)
                return 2
            day = row[0] - 1
        result = compare(conn, settings.sim_seed, day)
    print(json.dumps(result, indent=2))
    if args.save:
        RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    same = result["orders_in_process"] == result["orders_stored"]
    return 0 if same and result["rules"]["same"] and result["ml"]["same"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Simulator CLI.

    python -m tripwire.sim --seed 42 --days 1                  # replay at max speed
    python -m tripwire.sim --seed 42 --mode realtime --speed 5  # paced
    python -m tripwire.sim --seed 42 --clean                    # no manipulation

Writes the reference data and per-day labels, and prints a summary per day including the
event-stream hash used by the determinism check.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import msgspec

from tripwire.common.codec import StreamHasher
from tripwire.common.events import Event
from tripwire.common.reference import write_reference
from tripwire.sim.config import CLEAN_DAY, SimConfig
from tripwire.sim.day import simulate_day
from tripwire.sim.labels import labels_path, write_labels
from tripwire.sim.population import build_population, reference_data


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tripwire.sim")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--first-day", type=int, default=0)
    parser.add_argument("--mode", choices=("replay", "realtime"), default="replay")
    parser.add_argument("--speed", type=float, default=1.0, help="realtime only")
    parser.add_argument("--minutes", type=int, help="override session length")
    parser.add_argument("--clean", action="store_true", help="no manipulation episodes")
    parser.add_argument("--out", type=Path, help="write length-prefixed msgpack events here")
    parser.add_argument("--no-write", action="store_true", help="skip reference/labels files")
    args = parser.parse_args()

    config = SimConfig()
    if args.minutes:
        config = msgspec.structs.replace(config, session_minutes=args.minutes)
    if args.clean:
        config = msgspec.structs.replace(config, episodes=CLEAN_DAY)
    population = build_population(config)
    if not args.no_write:
        write_reference(reference_data(config, population))

    speed = args.speed if args.mode == "realtime" else None
    out = args.out.open("wb") if args.out else None
    encoder = msgspec.msgpack.Encoder()
    try:
        for day in range(args.first_day, args.first_day + args.days):
            hasher = StreamHasher()
            counts: Counter[str] = Counter()

            def sink(
                event: Event, hasher: StreamHasher = hasher, counts: Counter[str] = counts
            ) -> None:
                hasher(event)
                counts[type(event).__name__] += 1
                if out is not None:
                    data = encoder.encode(event)
                    out.write(len(data).to_bytes(4, "little") + data)

            started = time.perf_counter()
            result = simulate_day(config, args.seed, day, sink, speed, population)
            elapsed = time.perf_counter() - started
            if not args.no_write:
                write_labels(labels_path(args.seed, day), result.episodes)
            summary = {
                "seed": args.seed,
                "day": day,
                "events": result.event_count,
                "by_type": dict(sorted(counts.items())),
                "episodes": dict(Counter(ep.type for ep in result.episodes)),
                "elapsed_s": round(elapsed, 2),
                "events_per_s": round(result.event_count / elapsed),
                "stream_hash": hasher.hexdigest(),
            }
            print(json.dumps(summary))
    finally:
        if out is not None:
            out.close()


if __name__ == "__main__":
    main()

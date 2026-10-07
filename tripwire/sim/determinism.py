"""Determinism check: simulate the same seeded day in two fresh interpreters with
different hash randomisation and require identical event-stream hashes."""

from __future__ import annotations

import json
import os
import subprocess
import sys

SEED = 7
MINUTES = 20


def run_once(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    cmd = [
        sys.executable,
        "-m",
        "tripwire.sim",
        "--seed",
        str(SEED),
        "--minutes",
        str(MINUTES),
        "--no-write",
    ]
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout
    return str(json.loads(out.strip().splitlines()[-1])["stream_hash"])


def main() -> int:
    first, second = run_once("1"), run_once("2")
    ok = first == second
    print(
        f"determinism: seed={SEED} minutes={MINUTES} run1={first[:16]} run2={second[:16]} "
        f"{'OK' if ok else 'MISMATCH'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

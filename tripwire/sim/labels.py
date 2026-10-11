"""Ground-truth labels for injected manipulation episodes.

Labels are written ONLY to `tripwire/eval/labels/*.jsonl`. They never enter the event
stream, Kafka, the detectors or the ML model; `tests/test_leakage.py` enforces that.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import msgspec

from tripwire.sim.episode import Episode

LABELS_DIR = Path(__file__).resolve().parent.parent / "eval" / "labels"


def labels_path(seed: int, day: int, directory: Path = LABELS_DIR) -> Path:
    return directory / f"seed{seed}_day{day:03d}.jsonl"


def write_labels(path: Path, episodes: Iterable[Episode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = msgspec.json.Encoder()
    with path.open("wb") as fh:
        for episode in episodes:
            fh.write(encoder.encode(episode) + b"\n")


def read_labels(path: Path) -> list[Episode]:
    decoder = msgspec.json.Decoder(Episode)
    with path.open("rb") as fh:
        return [decoder.decode(line) for line in fh if line.strip()]

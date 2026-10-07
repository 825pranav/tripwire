"""Ground-truth labels for injected manipulation episodes.

Labels are written ONLY to `tripwire/eval/labels/*.jsonl`. They never enter the event
stream, Kafka, the detectors or the ML model; `tests/test_leakage.py` enforces that.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import msgspec

LABELS_DIR = Path(__file__).resolve().parent.parent / "eval" / "labels"


class Episode(msgspec.Struct):
    episode_id: str
    type: str  # spoofing | layering | wash | marking_close
    symbol: str
    owner: str
    accounts: list[str]
    start_ns: int
    end_ns: int
    order_ids: list[int] = msgspec.field(default_factory=list)

    def touch(self, ts: int, order_id: int | None = None) -> None:
        """Extend the episode to `ts` and record an order that belongs to it."""
        self.end_ns = max(self.end_ns, ts)
        if order_id is not None:
            self.order_ids.append(order_id)


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

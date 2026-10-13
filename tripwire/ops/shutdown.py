"""Graceful stop for the services.

`docker compose stop` sends SIGTERM, which by default ends a Python process without running
`finally` blocks. A Kafka consumer that is not closed stays in its group until the session
times out (45 s), and the restarted service gets no partitions until then. Turning SIGTERM
into `SystemExit` lets each service close its consumer and flush what it holds.
"""

from __future__ import annotations

import signal
from types import FrameType


def _exit(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(0)


def exit_on_sigterm() -> None:
    signal.signal(signal.SIGTERM, _exit)

"""msgpack serialization for events, plus a stable hash over an event stream."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import msgspec

from tripwire.common.events import Event

_encoder = msgspec.msgpack.Encoder()
_decoder: msgspec.msgpack.Decoder[Event] = msgspec.msgpack.Decoder(Event)


def encode(event: Event) -> bytes:
    return _encoder.encode(event)


def decode(data: bytes) -> Event:
    return _decoder.decode(data)


class StreamHasher:
    """Incremental SHA-256 over length-prefixed encoded events."""

    __slots__ = ("_hash", "count")

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self.count = 0

    def __call__(self, event: Event) -> None:
        data = _encoder.encode(event)
        self._hash.update(len(data).to_bytes(4, "little"))
        self._hash.update(data)
        self.count += 1

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


def stream_hash(events: Iterable[Event]) -> str:
    hasher = StreamHasher()
    for event in events:
        hasher(event)
    return hasher.hexdigest()

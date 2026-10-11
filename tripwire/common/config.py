"""Runtime settings from environment variables (see `.env.example`)."""

from __future__ import annotations

import os

import msgspec


class Settings(msgspec.Struct, frozen=True):
    kafka_bootstrap: str = "localhost:9092"
    database_url: str = "postgresql://tripwire:tripwire@localhost:5432/tripwire"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    sim_seed: int = 42
    sim_speed: float = 5.0  # simulated seconds per wall second
    investigator_concurrency: int = 1
    investigator_queue_cap: int = 50
    api_port: int = 8000


def load_settings() -> Settings:
    values: dict[str, str] = {}
    for field in msgspec.structs.fields(Settings):
        raw = os.environ.get(field.name.upper())
        if raw is not None and raw != "":
            values[field.name] = raw
    return msgspec.convert(values, type=Settings, strict=False)

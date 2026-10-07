# Tripwire. Targets are added phase by phase; `make check` runs after every phase.

.PHONY: help install lint format typecheck test test-full determinism check check-full sim-replay

help:
	@echo "install      uv sync (all dependency groups)"
	@echo "lint         ruff check + ruff format --check"
	@echo "format       ruff format + ruff check --fix"
	@echo "typecheck    mypy (strict on exchange/detectors/common)"
	@echo "test         fast pytest suite (excludes slow)"
	@echo "check        lint + typecheck + fast tests + determinism (< ~60s)"
	@echo "check-full   check + slow tests"
	@echo "sim-replay   simulate one seeded day at max speed"

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest -q -m "not slow"

test-full:
	uv run pytest -q

determinism:
	uv run python -m tripwire.sim.determinism

check: lint typecheck test determinism

check-full: lint typecheck test-full determinism

sim-replay:
	uv run python -m tripwire.sim --seed 42 --days 1

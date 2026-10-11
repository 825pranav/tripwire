# Tripwire. Targets are added phase by phase; `make check` runs after every phase.

.PHONY: help install lint format typecheck test test-full determinism smoke-eval eval train check check-full sim-replay up down sim logs doctor

help:
	@echo "install      uv sync (all dependency groups)"
	@echo "lint         ruff check + ruff format --check"
	@echo "format       ruff format + ruff check --fix"
	@echo "typecheck    mypy (strict on exchange/detectors/common)"
	@echo "test         fast pytest suite (excludes slow)"
	@echo "train        fit the Isolation Forest on simulated clean days"
	@echo "eval         rules, ML, union and fusion on 10 seeded days -> tripwire/eval/results"
	@echo "check        lint + typecheck + fast tests + determinism + smoke eval (< ~60s)"
	@echo "check-full   check + slow tests + 20-day eval, diffed against the last full run"
	@echo "sim-replay   simulate one seeded day at max speed (no Kafka)"
	@echo "up           start Kafka, Postgres and the services (docker compose)"
	@echo "sim          start the realtime simulator feeding Kafka"
	@echo "down         stop everything (keeps the database volume)"
	@echo "logs         follow service logs"
	@echo "doctor       pass/fail health table for the running system"

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

smoke-eval:
	uv run python -m tripwire.eval --smoke

train:
	uv run python -m tripwire.ml.train --days 10

eval:
	uv run python -m tripwire.eval --days 10

check: lint typecheck test determinism smoke-eval

check-full: lint typecheck test-full determinism smoke-eval
	uv run python -m tripwire.eval --days 20 --full

sim-replay:
	uv run python -m tripwire.sim --seed 42 --days 1

up:
	docker compose up -d --build

sim:
	docker compose --profile sim up -d sim

down:
	docker compose --profile sim --profile debug down

logs:
	docker compose logs -f --tail 50

doctor:
	uv run python -m tripwire.ops.doctor

# Tripwire. Targets are added phase by phase; `make check` runs after every phase.

.PHONY: help install lint format typecheck test test-full determinism smoke-eval propose-baseline approve-baseline eval train check check-full sim-replay start up down sim logs doctor parity demo frontend frontend-build bench grafana

help:
	@echo "install      uv sync (all dependency groups)"
	@echo "lint         ruff check + ruff format --check"
	@echo "format       ruff format + ruff check --fix"
	@echo "typecheck    mypy (strict on exchange/detectors/common)"
	@echo "test         fast pytest suite (excludes slow)"
	@echo "propose-baseline / approve-baseline   re-measure the smoke baseline / adopt it"
	@echo "train        fit the Isolation Forest on simulated clean days"
	@echo "eval         rules, ML, union and fusion on 10 seeded days -> tripwire/eval/results"
	@echo "bench        engine, detector and end-to-end Kafka throughput + alert latency"
	@echo "check        lint + typecheck + fast tests + determinism + smoke eval (< ~60s)"
	@echo "check-full   check + slow tests + 20-day eval, diffed against the last full run"
	@echo "sim-replay   simulate one seeded day at max speed (no Kafka)"
	@echo "start        one command: Docker + Ollama + stack + simulator, wait healthy, open dashboard"
	@echo "up           start Kafka, Postgres and the services (docker compose)"
	@echo "sim          start the realtime simulator feeding Kafka"
	@echo "down         stop everything (keeps the database volume)"
	@echo "logs         follow service logs"
	@echo "doctor       pass/fail health table for the running system"
	@echo "grafana      open the Grafana operations dashboard (Prometheus on :9090)"
	@echo "parity       live alerts for the last streamed day vs the same day in-process"
	@echo "demo         alias for start"
	@echo "frontend     run the Vite dev server (proxying the API on :8000)"

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

propose-baseline:
	uv run python -m tripwire.eval --smoke --propose-baseline

approve-baseline:
	uv run python -c "import shutil; shutil.copy('tripwire/eval/results/baseline.proposed.json', 'tripwire/eval/results/baseline.json')"

train:
	uv run python -m tripwire.ml.train --days 10

eval:
	uv run python -m tripwire.eval --days 10 --citations 10

check: lint typecheck test determinism smoke-eval

check-full: lint typecheck test-full determinism smoke-eval frontend-build
	uv run python -m tripwire.eval --days 20 --full --citations 10
	docker compose up -d --wait kafka
	uv run python -m tripwire.eval.bench --label check-full

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

parity:
	uv run python -m tripwire.ops.parity

grafana:
	uv run python -c "import webbrowser; webbrowser.open('http://localhost:3000/d/tripwire-ops')"

start:
	uv run python -m tripwire.ops.start

demo: start

frontend:
	cd frontend && npm install && npm run dev

frontend-build:
	cd frontend && npm ci --no-audit --no-fund && npm run build

bench:
	docker compose up -d --wait kafka
	uv run python -m tripwire.eval.bench

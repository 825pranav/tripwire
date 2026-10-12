"""FastAPI app: REST for alerts, case reports, replay, metrics and health; a WebSocket for
live book snapshots, trades and alerts. Pydantic is used only here, at the boundary."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from confluent_kafka.admin import AdminClient
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tripwire.api import queries
from tripwire.api.live import LiveHub
from tripwire.common.config import Settings, load_settings
from tripwire.common.db import apply_schema, connect
from tripwire.common.kafka import ALERTS, ALL_TOPICS, EVENT_TOPICS
from tripwire.common.reference import load_reference
from tripwire.ops.doctor import consumer_lag
from tripwire.ops.heartbeat import Heartbeat

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "eval" / "results"
STALE_HEARTBEAT_S = 15.0


class Alert(BaseModel):
    alert_id: str
    type: str
    symbol: str
    accounts: list[str]
    window_start_ns: int
    window_end_ns: int
    order_ids: list[int]
    score: float
    features: dict[str, float]
    emitted_ts_ns: int
    source: str
    status: str
    report_status: str | None = None


class CaseReport(BaseModel):
    alert_id: str
    status: str
    model: str
    report: dict[str, Any]
    regulations: dict[str, Any]
    attempts: int
    citations_total: int
    citations_valid: int
    validation_errors: list[str]
    latency_ms: int


class AlertDetail(BaseModel):
    alert: Alert
    report: CaseReport | None


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str


class Readiness(BaseModel):
    ready: bool
    dependencies: list[DependencyStatus]


def check_dependencies(settings: Settings, pool: queries.Pool) -> list[DependencyStatus]:
    out = []
    try:
        admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
        topics = set(admin.list_topics(timeout=3).topics)
        missing = [t for t in ALL_TOPICS if t not in topics]
        out.append(
            DependencyStatus(
                name="kafka", ok=not missing, detail=f"missing {missing}" if missing else "ok"
            )
        )
    except Exception as exc:
        out.append(DependencyStatus(name="kafka", ok=False, detail=str(exc)[:200]))
    try:
        with pool.conn() as conn:
            conn.execute("SELECT 1")
        out.append(DependencyStatus(name="postgres", ok=True, detail="ok"))
    except Exception as exc:
        out.append(DependencyStatus(name="postgres", ok=False, detail=str(exc)[:200]))
    try:
        with urllib.request.urlopen(f"{settings.ollama_host}/api/tags", timeout=3) as resp:
            names = {m["name"] for m in json.load(resp)["models"]}
        model = settings.ollama_model
        pulled = model in names or f"{model}:latest" in names
        detail = model if pulled else f"{model} not pulled"
        out.append(DependencyStatus(name="ollama", ok=pulled, detail=detail))
    except Exception as exc:
        out.append(DependencyStatus(name="ollama", ok=False, detail=str(exc)[:200]))
    return out


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    pool = queries.Pool(settings.database_url)
    hub = LiveHub(settings.kafka_bootstrap)
    stop = threading.Event()

    def heartbeat_loop() -> None:
        conn = connect(settings.database_url)
        apply_schema(conn)
        beat = Heartbeat(conn, "api")
        while not stop.wait(1.0):
            beat.processed = hub.forwarded
            beat.last_event_ns = hub.last_event_ns
            beat.maybe_write(websocket_clients=len(hub.clients))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.start(asyncio.get_running_loop())
        threading.Thread(target=heartbeat_loop, name="heartbeat", daemon=True).start()
        yield
        stop.set()
        hub.stop()

    app = FastAPI(title="Tripwire", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", response_model=Readiness)
    def ready() -> JSONResponse:
        deps = check_dependencies(settings, pool)
        body = Readiness(ready=all(d.ok for d in deps), dependencies=deps)
        return JSONResponse(body.model_dump(), status_code=200 if body.ready else 503)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        """Everything the dashboard status bar needs, in one call."""
        deps = check_dependencies(settings, pool)
        with pool.conn() as conn:
            services = queries.service_health(conn)
        for svc in services:
            svc["fresh"] = svc["age_s"] < STALE_HEARTBEAT_S
        lag: dict[str, int | None] = {}
        for group, topics in (("detectors", EVENT_TOPICS), ("investigator", (ALERTS,))):
            try:
                lag[group] = consumer_lag(settings, group, topics)
            except Exception:
                lag[group] = None
        return {
            "dependencies": [d.model_dump() for d in deps],
            "services": services,
            "lag": lag,
        }

    @app.get("/api/symbols")
    def symbols() -> list[dict[str, Any]]:
        return [
            {"symbol": s.symbol, "tick_size": s.tick_size, "reference_price": s.reference_price}
            for s in load_reference().symbols
        ]

    @app.get("/api/alerts", response_model=list[Alert])
    def alerts(
        type: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = Query(100, le=1000),
    ) -> list[dict[str, Any]]:
        with pool.conn() as conn:
            return queries.list_alerts(conn, type, symbol, status, limit)

    @app.get("/api/alerts/{alert_id}", response_model=AlertDetail)
    def alert_detail(alert_id: str) -> dict[str, Any]:
        with pool.conn() as conn:
            alert = queries.get_alert(conn, alert_id)
            if alert is None:
                raise HTTPException(404, "alert not found")
            return {"alert": alert, "report": queries.get_report(conn, alert_id)}

    @app.get("/api/alerts/{alert_id}/report", response_model=CaseReport)
    def alert_report(alert_id: str) -> dict[str, Any]:
        with pool.conn() as conn:
            report = queries.get_report(conn, alert_id)
        if report is None:
            raise HTTPException(404, "no case report yet")
        return report

    @app.get("/api/alerts/{alert_id}/replay")
    def alert_replay(alert_id: str) -> dict[str, Any]:
        with pool.conn() as conn:
            alert = queries.get_alert(conn, alert_id)
            if alert is None:
                raise HTTPException(404, "alert not found")
            start = alert["window_start_ns"] - queries.PADDING_NS
            end = alert["window_end_ns"] + queries.PADDING_NS
            data = queries.replay(conn, alert["symbol"], start, end, alert["accounts"])
        return {"symbol": alert["symbol"], "start_ns": start, "end_ns": end, **data}

    @app.get("/api/replay")
    def replay(
        symbol: str,
        start_ns: int,
        end_ns: int,
        accounts: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        if end_ns - start_ns > 30 * 60 * 1_000_000_000:
            raise HTTPException(400, "window longer than 30 minutes")
        with pool.conn() as conn:
            return queries.replay(conn, symbol, start_ns, end_ns, accounts or [])

    @app.get("/api/market/{symbol}")
    def market(symbol: str, trades: int = Query(500, le=5000)) -> dict[str, Any]:
        """Initial state for the live view: recent trades and the latest book."""
        with pool.conn() as conn:
            return {
                "trades": queries.recent_trades(conn, symbol, trades),
                "book": queries.latest_snapshot(conn, symbol),
            }

    @app.get("/api/metrics")
    def metrics() -> dict[str, Any]:
        with pool.conn() as conn:
            live = queries.metrics(conn)
        evals = {}
        for name in ("full_run", "eval_10days"):
            path = RESULTS_DIR / f"{name}.json"
            if path.exists():
                evals[name] = json.loads(path.read_text(encoding="utf-8"))
        return {"live": live, "eval": evals}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        q = hub.subscribe()
        try:
            while True:
                await socket.send_text(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(q)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app

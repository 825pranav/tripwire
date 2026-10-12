import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from tripwire.api.app import create_app
from tripwire.api.live import to_message
from tripwire.common.codec import encode
from tripwire.common.config import Settings
from tripwire.common.events import BookSnapshot, OrderAccepted, OrderType, Side, Trade

SETTINGS = Settings(
    kafka_bootstrap="localhost:1",  # nothing listens here: dependency checks must fail
    ollama_host="http://localhost:1",
)


def postgres_available() -> bool:
    try:
        psycopg.connect(Settings().database_url, connect_timeout=2).close()
    except psycopg.OperationalError:
        return False
    return True


def test_health_does_not_touch_dependencies() -> None:
    client = TestClient(create_app(SETTINGS))  # no lifespan: no Kafka thread, no DB
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.slow  # waits on connection timeouts to three unreachable services
def test_ready_reports_unreachable_dependencies() -> None:
    settings = Settings(
        kafka_bootstrap="localhost:1",
        ollama_host="http://localhost:1",
        database_url="postgresql://x:y@localhost:1/z",
    )
    response = TestClient(create_app(settings)).get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert {d["name"] for d in body["dependencies"]} == {"kafka", "postgres", "ollama"}
    assert not any(d["ok"] for d in body["dependencies"])


def test_symbols_come_from_reference_data() -> None:
    response = TestClient(create_app(SETTINGS)).get("/api/symbols")
    assert [s["symbol"] for s in response.json()] == ["ACME", "BOLT", "CRUX"]


@pytest.mark.skipif(not postgres_available(), reason="needs the compose Postgres")
def test_alert_endpoints_against_postgres() -> None:
    client = TestClient(create_app(Settings(kafka_bootstrap="localhost:1")))
    alerts = client.get("/api/alerts", params={"limit": 5})
    assert alerts.status_code == 200
    assert client.get("/api/alerts/does-not-exist").status_code == 404
    for alert in alerts.json()[:2]:
        detail = client.get(f"/api/alerts/{alert['alert_id']}").json()
        assert detail["alert"]["alert_id"] == alert["alert_id"]
        replay = client.get(f"/api/alerts/{alert['alert_id']}/replay").json()
        assert replay["start_ns"] < replay["end_ns"]


def test_live_messages() -> None:
    trade = Trade(1, 2, "ACME", 10_000, 100, Side.BUY, 3, 4, "A", "B")
    assert to_message("executions", encode(trade)) == {
        "kind": "trade",
        "data": {
            "type": "trd", "ts": 1, "trade_id": 2, "symbol": "ACME", "price": 10_000, "qty": 100,
            "aggressor_side": "B", "buy_order_id": 3, "sell_order_id": 4,
            "buy_account": "A", "sell_account": "B",
        },
    }  # fmt: skip
    snap = BookSnapshot(5, "ACME", ((9_999, 300),), ((10_001, 200),))
    assert to_message("book_snapshots", encode(snap))["kind"] == "book"  # type: ignore[index]
    order = OrderAccepted(1, 2, "ACME", "A", Side.BUY, OrderType.LIMIT, 100, 100)
    assert to_message("orders", encode(order)) is None
    alert = json.dumps({"alert_id": "x"}).encode()
    assert to_message("alerts", alert) == {"kind": "alert", "data": {"alert_id": "x"}}

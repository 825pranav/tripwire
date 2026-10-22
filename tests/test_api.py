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


def beat(service: str, last_event_ns: int | None, processed: int, age_s: float, **details: int):
    return {
        "service": service,
        "last_event_ns": last_event_ns,
        "processed": processed,
        "details": details,
        "age_s": age_s,
    }


def test_metrics_render_doctor_signals_in_prometheus_format() -> None:
    from prometheus_client.parser import text_string_to_metric_families

    from tripwire.api.metrics import Snapshot, render

    snap = Snapshot(
        dependencies={"kafka": True, "postgres": True, "ollama": False},
        services=[
            beat("sim", 300 * 10**9, 900, 1.5),
            beat("detectors", 240 * 10**9, 880, 2.0),
            beat("investigator", None, 3, 40.0, queue_depth=7, queue_cap=50),
        ],
        lag={"detectors": 120, "investigator": None},
        alerts=[{"type": "spoofing", "source": "rules", "n": 4}],
        reports=[
            {"status": "verified", "n": 2, "citations": 10, "valid": 9, "median_latency_ms": 51_000}
        ],
    )
    samples = {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for family in text_string_to_metric_families(render(snap).decode())
        for s in family.samples
    }
    assert samples[("tripwire_dependency_up", (("dependency", "ollama"),))] == 0
    assert samples[("tripwire_dependency_up", (("dependency", "kafka"),))] == 1
    assert samples[("tripwire_heartbeat_age_seconds", (("service", "investigator"),))] == 40.0
    assert samples[("tripwire_processed_events_total", (("service", "sim"),))] == 900
    assert samples[("tripwire_detectors_behind_simulator_seconds", ())] == 60.0
    assert samples[("tripwire_investigator_queue_depth", ())] == 7
    assert samples[("tripwire_consumer_lag_messages", (("group", "detectors"),))] == 120
    assert ("tripwire_consumer_lag_messages", (("group", "investigator"),)) not in samples
    assert samples[("tripwire_alerts_total", (("source", "rules"), ("type", "spoofing")))] == 4
    assert (
        samples[
            ("tripwire_case_report_citations_total", (("status", "verified"), ("valid", "false")))
        ]
        == 1
    )
    assert (
        samples[("tripwire_case_report_latency_median_seconds", (("status", "verified"),))] == 51.0
    )


def test_metrics_endpoint_survives_dependencies_being_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import tripwire.api.app as app_module

    down = [
        app_module.DependencyStatus(name=n, ok=False, detail="down")
        for n in ("kafka", "postgres", "ollama")
    ]
    monkeypatch.setattr(app_module, "check_dependencies", lambda settings, pool: down)
    settings = Settings(
        database_url="postgresql://x:y@localhost:1/z", kafka_bootstrap="localhost:1"
    )
    response = TestClient(create_app(settings)).get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'tripwire_dependency_up{dependency="postgres"} 0.0' in response.text
    assert "tripwire_heartbeat_age_seconds" not in response.text  # postgres down: no heartbeats

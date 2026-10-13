import json
import logging
import random

import pytest

from tripwire.common.config import Settings, load_settings
from tripwire.common.events import BookSnapshot, Event, Side, Trade
from tripwire.common.kafka import SymbolReorderer, header
from tripwire.common.logs import JsonFormatter
from tripwire.ops.doctor import check_detector_progress, check_heartbeat


def snap(ts: int, symbol: str = "AAA") -> Event:
    return BookSnapshot(ts=ts, symbol=symbol, bids=(), asks=())


def test_reorderer_restores_per_symbol_order_from_shuffled_input() -> None:
    # Each symbol's stream starts at its first message, so seq 0 leads; the rest is shuffled.
    rest = [(sym, i, snap(i, sym)) for sym in ("AAA", "BBB") for i in range(1, 200)]
    random.Random(0).shuffle(rest)
    shuffled = [("AAA", 0, snap(0, "AAA")), ("BBB", 0, snap(0, "BBB")), *rest]
    reorderer = SymbolReorderer()
    out: dict[str, list[int]] = {"AAA": [], "BBB": []}
    for sym, seq, event in shuffled:
        for ready in reorderer.push(sym, b"1", seq, event):
            out[sym].append(ready.ts)
    assert out["AAA"] == list(range(200))
    assert out["BBB"] == list(range(200))
    assert reorderer.buffered() == 0


def test_reorderer_waits_for_the_start_of_a_new_run() -> None:
    reorderer = SymbolReorderer(max_pending=100)
    assert reorderer.push("AAA", b"5", 2, snap(2)) == []  # seq 0 and 1 are on other topics
    assert reorderer.push("AAA", b"5", 0, snap(0)) == [snap(0)]
    assert [e.ts for e in reorderer.push("AAA", b"5", 1, snap(1))] == [1, 2]


def test_reorderer_joins_mid_stream_and_drops_older_or_duplicate() -> None:
    reorderer = SymbolReorderer(sources=1, max_pending=100)
    assert [e.ts for e in reorderer.push("AAA", b"5", 500, snap(500))] == [500]
    assert reorderer.push("AAA", b"5", 499, snap(499)) == []
    assert reorderer.push("AAA", b"5", 500, snap(500)) == []
    assert reorderer.push("AAA", b"5", 502, snap(502)) == []
    assert [e.ts for e in reorderer.push("AAA", b"5", 501, snap(501))] == [501, 502]


def trade(ts: int) -> Event:
    return Trade(ts, ts, "AAA", 100, 1, Side.BUY, 1, 2, "a", "b")


def test_reorderer_resumes_after_a_restart_from_committed_offsets() -> None:
    # A restarted consumer resumes each topic at its own committed offset: here snapshots
    # from seq 1000 and trades from seq 1003 (trades 1000-1002 were already processed).
    # The stream can only be gap-free from 1003, the highest first seq over the sources.
    reorderer = SymbolReorderer(sources=2, max_pending=100)
    assert reorderer.push("AAA", b"5", 1000, snap(1000)) == []
    assert reorderer.push("AAA", b"5", 1001, snap(1001)) == []
    assert reorderer.push("AAA", b"5", 1004, snap(1004)) == []
    assert [e.ts for e in reorderer.push("AAA", b"5", 1003, trade(1003))] == [1003, 1004]
    assert reorderer.push("AAA", b"5", 1002, snap(1002)) == []  # before the join point
    assert [e.ts for e in reorderer.push("AAA", b"5", 1005, trade(1005))] == [1005]


def test_reorderer_joins_without_a_silent_source() -> None:
    reorderer = SymbolReorderer(sources=2, max_pending=100, join_cap=3)
    assert reorderer.push("AAA", b"5", 700, snap(700)) == []
    assert reorderer.push("AAA", b"5", 701, snap(701)) == []
    assert [e.ts for e in reorderer.push("AAA", b"5", 702, snap(702))] == [700, 701, 702]


def test_reorderer_resets_on_new_run_and_drops_the_old_one() -> None:
    reorderer = SymbolReorderer()
    reorderer.push("AAA", b"1", 10, snap(10))
    assert [e.ts for e in reorderer.push("AAA", b"2", 0, snap(0))] == [0]
    assert reorderer.push("AAA", b"1", 11, snap(11)) == []  # old run still draining
    assert [e.ts for e in reorderer.push("AAA", b"2", 1, snap(1))] == [1]


def test_reorderer_skips_a_gap_that_never_fills() -> None:
    reorderer = SymbolReorderer(max_pending=3)
    reorderer.push("AAA", b"5", 0, snap(0))
    for seq in (2, 3, 4):
        assert reorderer.push("AAA", b"5", seq, snap(seq)) == []
    assert [e.ts for e in reorderer.push("AAA", b"5", 5, snap(5))] == [2, 3, 4, 5]


def test_header_lookup() -> None:
    headers: list[tuple[str, str | bytes | None]] = [("run", b"x"), ("seq", "7")]
    assert header(headers, "seq") == b"7"
    assert header(headers, "missing") is None
    assert header(None, "run") is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_SPEED", "2.5")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    monkeypatch.setenv("INVESTIGATOR_QUEUE_CAP", "")
    settings = load_settings()
    assert settings.sim_speed == 2.5
    assert settings.ollama_model == "llama3.2"
    assert settings.investigator_queue_cap == Settings().investigator_queue_cap


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.makeLogRecord({"msg": "alert", "levelname": "INFO", "name": "x"})
    record.alert_id = "a1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "alert" and payload["alert_id"] == "a1"
    assert payload["level"] == "info"


def test_doctor_heartbeat_freshness() -> None:
    beats = {"sim": {"age_s": 3.0, "processed": 10}, "old": {"age_s": 60.0, "processed": 1}}
    assert check_heartbeat(beats, "sim")[0] == "PASS"
    assert check_heartbeat(beats, "old")[0] == "FAIL"
    assert check_heartbeat(beats, "missing") == ("FAIL", "no heartbeat")


def test_doctor_fails_when_detectors_process_nothing() -> None:
    minute = 60 * 1_000_000_000
    sim = {"age_s": 1.0, "processed": 5_000, "last_event_ns": 100 * minute}
    stalled = {"age_s": 1.0, "processed": 0, "last_event_ns": None}
    assert check_detector_progress({"sim": sim, "detectors": stalled})[0] == "FAIL"
    behind = {"age_s": 1.0, "processed": 10, "last_event_ns": 90 * minute}
    assert check_detector_progress({"sim": sim, "detectors": behind})[0] == "FAIL"
    current = {"age_s": 1.0, "processed": 4_990, "last_event_ns": 100 * minute - 10**9}
    assert check_detector_progress({"sim": sim, "detectors": current})[0] == "PASS"

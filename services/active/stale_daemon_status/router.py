# deps: fastapi, pydantic, requests
"""FastAPI router for stale daemon status detection.

Queries the write_service service_health table (DuckDB store) to identify
daemons whose last heartbeat exceeds the configured stale threshold.
Public access, no auth required.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["stale_daemon_status"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Stale threshold (seconds) per daemon name.
# Daemon is STALE if its heartbeat age > this value.
THRESHOLD_MAP: dict[str, int] = {
    "write_service": 300,
    "inference_router": 120,
    "manager_agent": 120,
    "pipeline_bridge": 120,
    "t2_consumer": 120,
    "zo_sentinel_builder": 600,
    "sentinel_directive_generator": 7500,
    "gate_scheduler": 60,
    "self_diagnostics": 600,
    "build_watcher_api": 600,
    "mcp_scanner": 14400,
    "signal_analyser": 120,
    "trust_synthesiser": 600,
    "threat_intel_ingestor": 600,
    "attestation_engine": 600,
    "rug_pull_monitor": 28800,
    "risk_ranker": 600,
    "world_article_feeder": 600,
    "data_velocity": 120,
    "anti_entropy": 14400,
    "wisdom_synthesiser": 14400,
    "gate_orchestrator": 14400,
}


class StaleDaemonEntry(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int


class StaleDaemonStatusResponse(BaseModel):
    stale_count: int
    stale_daemons: List[StaleDaemonEntry]


def _query_service_health() -> List[dict]:
    """Query write_service service_health table via HTTP POST."""
    payload = {"sql": f"SELECT service, status, last_heartbeat FROM {SERVICE_HEALTH_TABLE}", "params": []}
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to query write_service: {exc}")
    data = resp.json()
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail="Malformed response from write_service")
    return rows


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp; handle Z suffix."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/daemons/stale", response_model=StaleDaemonStatusResponse)
def stale_daemon_status() -> StaleDaemonStatusResponse:
    """Return all daemons whose last heartbeat is stale."""
    rows = _query_service_health()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    stale_daemons: List[StaleDaemonEntry] = []

    for row in rows:
        name = row.get("service")
        raw_ts = row.get("last_heartbeat")
        status = row.get("status", "unknown")

        if not name or not raw_ts:
            continue

        try:
            hb = _parse_timestamp(raw_ts)
        except Exception:
            continue

        age_seconds = (now - hb).total_seconds()
        threshold = THRESHOLD_MAP.get(name, 300)

        if age_seconds > threshold:
            stale_daemons.append(StaleDaemonEntry(
                name=name,
                last_heartbeat=raw_ts,
                age_seconds=age_seconds,
                status=status,
                threshold_seconds=threshold,
            ))

    return StaleDaemonStatusResponse(
        stale_count=len(stale_daemons),
        stale_daemons=stale_daemons,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    class _FakeResponse:
        def __init__(self, json_data: dict):
            self._json = json_data
        def raise_for_status(self):
            pass
        def json(self):
            return self._json

    # Use actual wall-clock "now" as the anchor so age calculations are correct.
    _now_ts = datetime.now(timezone.utc)
    # "past" timestamps: 5 seconds ago and 500 seconds ago
    _recent = _now_ts.replace(second=_now_ts.second - 5, microsecond=0)
    _old = _now_ts.replace(second=_now_ts.second - 500, microsecond=0)
    _ts_fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")

    _test_rows = [
        # age ~5s, threshold 14400s → NOT stale
        {"service": "gate_orchestrator", "status": "ok", "last_heartbeat": _ts_fmt(_recent)},
        # age ~5s, threshold 120s → NOT stale
        {"service": "signal_analyser", "status": "degraded", "last_heartbeat": _ts_fmt(_recent)},
        # age ~0s, threshold 300s → NOT stale
        {"service": "write_service", "status": "healthy", "last_heartbeat": _ts_fmt(_now_ts)},
        # age ~500s, default threshold 300s → STALE
        {"service": "unknown_daemon", "status": "unknown", "last_heartbeat": _ts_fmt(_old)},
    ]

    def _fake_post(url, json, timeout):
        return _FakeResponse({"rows": _test_rows})

    _original_post = requests.post
    requests.post = _fake_post

    try:
        client = TestClient(app)
        response = client.get("/api/daemons/stale")
        assert response.status_code == 200, f"Unexpected status: {response.status_code}"

        data = response.json()
        assert "stale_count" in data, "Missing 'stale_count' key"
        assert "stale_daemons" in data, "Missing 'stale_daemons' key"

        # signal_analyser (120s, age 1800s) → STALE
        # unknown_daemon (default 300s, age 1800s) → STALE
        # gate_orchestrator (14400s, age 1800s) → NOT stale
        # write_service (300s, age 0s) → NOT stale
        assert data["stale_count"] == 2, f"stale_count mismatch: {data['stale_count']}"
        stale_names = {d["name"] for d in data["stale_daemons"]}
        assert "signal_analyser" in stale_names, f"signal_analyser should be stale: {stale_names}"
        assert "unknown_daemon" in stale_names, f"unknown_daemon should be stale: {stale_names}"
        assert "gate_orchestrator" not in stale_names, f"gate_orchestrator should NOT be stale: {stale_names}"
        assert "write_service" not in stale_names, f"write_service should NOT be stale: {stale_names}"

        # Verify age_seconds is roughly 1800
        ages = {d["name"]: d["age_seconds"] for d in data["stale_daemons"]}
        assert abs(ages.get("signal_analyser", -1) - 1800) < 5, f"signal_analyser age mismatch: {ages.get('signal_analyser')}"
        assert abs(ages.get("unknown_daemon", -1) - 1800) < 5, f"unknown_daemon age mismatch: {ages.get('unknown_daemon')}"

        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = _original_post

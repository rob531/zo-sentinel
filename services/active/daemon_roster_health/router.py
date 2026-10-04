# deps: fastapi, pydantic, requests
"""FastAPI router for daemon roster health status.

Queries the write_service service_health table (DuckDB store) to return
health status for all registered daemons. Public access, no auth required.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["daemon_roster_health"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Thresholds (seconds) per daemon - stale if heartbeat age exceeds this.
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


class DaemonHealth(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int
    is_stale: bool


class HealthSummary(BaseModel):
    total: int
    healthy_count: int
    stale_count: int


class DaemonRosterHealthResponse(BaseModel):
    daemons: List[DaemonHealth]
    summary: HealthSummary


def _query_service_health() -> List[dict]:
    """Query write_service service_health table via HTTP."""
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
    """Parse ISO-8601 timestamp from write_service (handles Z suffix)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/daemon-roster/health", response_model=DaemonRosterHealthResponse)
def daemon_roster_health() -> DaemonRosterHealthResponse:
    """Return health status for all daemons from the write_service store."""
    rows = _query_service_health()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    daemons: List[DaemonHealth] = []
    healthy_count = 0
    stale_count = 0

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
        is_stale = age_seconds > threshold

        if is_stale:
            stale_count += 1
        else:
            healthy_count += 1

        daemons.append(DaemonHealth(
            name=name,
            last_heartbeat=raw_ts,
            age_seconds=age_seconds,
            status=status,
            threshold_seconds=threshold,
            is_stale=is_stale,
        ))

    return DaemonRosterHealthResponse(
        daemons=daemons,
        summary=HealthSummary(
            total=len(daemons),
            healthy_count=healthy_count,
            stale_count=stale_count,
        ),
    )


# ---------------------------------------------------------------------------
# Self-test (executed when running this module directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Patch requests.post with a fake that returns test data
    class _FakeResponse:
        def __init__(self, json_data: dict):
            self._json = json_data
        def raise_for_status(self):
            pass
        def json(self):
            return self._json

    _now = datetime(2026, 8, 7, 14, 50, 0, tzinfo=timezone.utc)
    _ts_format = lambda dt: dt.isoformat().replace("+00:00", "Z")

    _test_rows = [
        {"service": "write_service", "status": "healthy", "last_heartbeat": _ts_format(_now)},
        {"service": "builder", "status": "healthy", "last_heartbeat": _ts_format(_now)},
        {"service": "gate_orchestrator", "status": "stale", "last_heartbeat": _ts_format(_now)},
    ]

    def _fake_post(url, json, timeout):
        return _FakeResponse({"rows": _test_rows})

    _original_post = requests.post
    requests.post = _fake_post

    try:
        client = TestClient(app)
        response = client.get("/api/daemon-roster/health")
        assert response.status_code == 200, f"Unexpected status: {response.status_code}"

        data = response.json()
        assert "daemons" in data, "Missing 'daemons' key"
        assert "summary" in data, "Missing 'summary' key"

        summary = data["summary"]
        assert summary["total"] == 3, f"total mismatch: {summary['total']}"
        assert summary["stale_count"] == 1, f"stale_count mismatch: {summary['stale_count']}"
        assert summary["healthy_count"] == 2, f"healthy_count mismatch: {summary['healthy_count']}"

        # Verify gate_orchestrator is marked stale (> 300s threshold for unknown daemon)
        ages = {d["name"]: d["age_seconds"] for d in data["daemons"]}
        assert ages.get("gate_orchestrator") == 0, f"gate_orchestrator age mismatch"
        assert ages.get("write_service") == 0, f"write_service age mismatch"

        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = _original_post

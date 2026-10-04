# deps: fastapi, pydantic, requests
"""FastAPI router for daemon health heartbeat management.

Allows daemons to register/update their heartbeat and query health status
for all registered daemons. Reads from / writes to the write_service
service_health DuckDB store via HTTP. Public access, no auth required.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["daemon_health_heartbeat_api"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Stale threshold (seconds) per daemon name.
# Daemon is STALE if its heartbeat age exceeds this value.
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


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class HeartbeatRequest(BaseModel):
    service: str
    status: str = "unknown"
    meta: Optional[str] = None


class HeartbeatResponse(BaseModel):
    service: str
    status: str
    last_heartbeat: str
    is_stale: bool


class DaemonHealthEntry(BaseModel):
    name: str
    last_heartbeat: Optional[str]
    age_seconds: Optional[float]
    status: str
    threshold_seconds: int
    is_stale: bool
    meta: Optional[str] = None


class DaemonHealthSummary(BaseModel):
    total: int
    healthy_count: int
    stale_count: int


class DaemonHealthResponse(BaseModel):
    daemons: List[DaemonHealthEntry]
    summary: DaemonHealthSummary


class StaleDaemonEntry(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int


class StaleDaemonResponse(BaseModel):
    stale_count: int
    stale_daemons: List[StaleDaemonEntry]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _query_service_health() -> List[dict]:
    """Query all rows from write_service service_health table."""
    payload = {"sql": f"SELECT service, status, last_heartbeat, meta FROM {SERVICE_HEALTH_TABLE}", "params": []}
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


def _upsert_heartbeat(service: str, status: str, meta: Optional[str]) -> dict:
    """Upsert a daemon heartbeat into write_service service_health table."""
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_sql = f"""
        INSERT INTO {SERVICE_HEALTH_TABLE} (service, status, last_heartbeat, meta)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (service) DO UPDATE SET
            status = excluded.status,
            last_heartbeat = excluded.last_heartbeat,
            meta = excluded.meta
    """
    payload = {
        "sql": upsert_sql,
        "params": [service, status, now_iso, meta],
        "wait": True,
    }
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/execute", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to upsert heartbeat: {exc}")
    return {"service": service, "status": status, "last_heartbeat": now_iso}


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp; handle Z suffix."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _get_stale_threshold(name: str) -> int:
    return THRESHOLD_MAP.get(name, 300)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/daemons/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(body: HeartbeatRequest) -> HeartbeatResponse:
    """Register or update a daemon's heartbeat."""
    result = _upsert_heartbeat(body.service, body.status, body.meta)
    threshold = _get_stale_threshold(body.service)
    # Immediately after upsert, age is ~0 so never stale on self-report
    return HeartbeatResponse(
        service=result["service"],
        status=result["status"],
        last_heartbeat=result["last_heartbeat"],
        is_stale=False,
    )


@router.get("/daemons/health", response_model=DaemonHealthResponse)
def get_daemons_health() -> DaemonHealthResponse:
    """Return health status for all registered daemons."""
    rows = _query_service_health()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    daemons: List[DaemonHealthEntry] = []
    healthy_count = 0
    stale_count = 0

    for row in rows:
        name = row.get("service")
        raw_ts = row.get("last_heartbeat")
        status = row.get("status", "unknown")
        meta = row.get("meta")

        if not name:
            continue

        threshold = _get_stale_threshold(name)
        age_seconds: Optional[float] = None
        is_stale = False

        if raw_ts:
            try:
                hb = _parse_timestamp(raw_ts)
                age_seconds = (now - hb).total_seconds()
                is_stale = age_seconds > threshold
            except Exception:
                pass

        if is_stale:
            stale_count += 1
        else:
            healthy_count += 1

        daemons.append(DaemonHealthEntry(
            name=name,
            last_heartbeat=raw_ts,
            age_seconds=age_seconds,
            status=status,
            threshold_seconds=threshold,
            is_stale=is_stale,
            meta=meta,
        ))

    return DaemonHealthResponse(
        daemons=daemons,
        summary=DaemonHealthSummary(
            total=len(daemons),
            healthy_count=healthy_count,
            stale_count=stale_count,
        ),
    )


@router.get("/daemons/stale", response_model=StaleDaemonResponse)
def get_stale_daemons() -> StaleDaemonResponse:
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
        threshold = _get_stale_threshold(name)

        if age_seconds > threshold:
            stale_daemons.append(StaleDaemonEntry(
                name=name,
                last_heartbeat=raw_ts,
                age_seconds=age_seconds,
                status=status,
                threshold_seconds=threshold,
            ))

    return StaleDaemonResponse(
        stale_count=len(stale_daemons),
        stale_daemons=stale_daemons,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    class _FakeResponse:
        def __init__(self, json_data: dict, status_code: int = 200):
            self._json = json_data
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

        def json(self):
            return self._json

    from datetime import timedelta
    _now_ts = datetime.now(timezone.utc)
    _ts_fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")

    # Timestamps relative to _now_ts using fixed deltas
    _recent = _now_ts - timedelta(seconds=5)
    _old = _now_ts - timedelta(seconds=500)

    _test_rows = [
        # age ~5s, threshold 14400s → NOT stale
        {"service": "gate_orchestrator", "status": "running", "last_heartbeat": _ts_fmt(_recent), "meta": None},
        # age ~5s, threshold 120s → NOT stale
        {"service": "signal_analyser", "status": "degraded", "last_heartbeat": _ts_fmt(_recent), "meta": None},
        # age ~0s, threshold 300s → NOT stale
        {"service": "write_service", "status": "running", "last_heartbeat": _ts_fmt(_now_ts), "meta": None},
        # age ~500s, threshold 120s → STALE
        {"service": "inference_router", "status": "ok", "last_heartbeat": _ts_fmt(_old), "meta": None},
        # age ~500s, default threshold 300s → STALE
        {"service": "unknown_daemon", "status": "unknown", "last_heartbeat": _ts_fmt(_old), "meta": None},
    ]

    _upsert_calls: List[dict] = []

    def _fake_post(url, json=None, timeout=None):
        if "/query" in url:
            return _FakeResponse({"rows": _test_rows})
        elif "/execute" in url:
            _upsert_calls.append(json)
            return _FakeResponse({})
        return _FakeResponse({}, 404)

    _original_post = requests.post
    requests.post = _fake_post

    try:
        client = TestClient(app)

        # --- Test 1: POST heartbeat ---
        resp = client.post("/api/daemons/heartbeat", json={"service": "test_daemon", "status": "ok"})
        assert resp.status_code == 200, f"heartbeat status: {resp.status_code} {resp.text}"
        hb_data = resp.json()
        assert hb_data["service"] == "test_daemon"
        assert hb_data["status"] == "ok"
        assert "last_heartbeat" in hb_data
        assert hb_data["is_stale"] is False
        assert len(_upsert_calls) == 1, f"Expected 1 upsert call, got {len(_upsert_calls)}"

        # --- Test 2: GET /daemons/health ---
        resp = client.get("/api/daemons/health")
        assert resp.status_code == 200, f"health status: {resp.status_code} {resp.text}"
        health_data = resp.json()
        assert "daemons" in health_data
        assert "summary" in health_data
        summary = health_data["summary"]
        assert summary["total"] == 5, f"total mismatch: {summary['total']}"
        # gate_orchestrator(5s,14400), signal_analyser(5s,120), write_service(0s,300) → healthy
        # inference_router(500s,120), unknown_daemon(500s,300) → stale
        assert summary["stale_count"] == 2, f"stale_count: {summary['stale_count']}"
        assert summary["healthy_count"] == 3, f"healthy_count: {summary['healthy_count']}"
        names = {d["name"] for d in health_data["daemons"]}
        assert "write_service" in names
        assert "inference_router" in names

        # --- Test 3: GET /daemons/stale ---
        resp = client.get("/api/daemons/stale")
        assert resp.status_code == 200, f"stale status: {resp.status_code} {resp.text}"
        stale_data = resp.json()
        assert stale_data["stale_count"] == 2, f"stale_count: {stale_data['stale_count']}"
        stale_names = {d["name"] for d in stale_data["stale_daemons"]}
        assert "inference_router" in stale_names, f"inference_router should be stale: {stale_names}"
        assert "unknown_daemon" in stale_names, f"unknown_daemon should be stale: {stale_names}"
        assert "gate_orchestrator" not in stale_names
        assert "write_service" not in stale_names

        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = _original_post

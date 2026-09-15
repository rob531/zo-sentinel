# deps: fastapi, pydantic, requests
"""FastAPI router for daemon heartbeat alerts.

Queries the write_service service_health table (DuckDB store) to emit
an alert whenever a daemon's last heartbeat exceeds its configured
stale threshold. Public access, no auth required.

Data plane: write_service (DuckDB) only; no app Postgres tables.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import List

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["daemon_heartbeat_alerts"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Threshold map: daemon name → stale threshold in seconds.
# A daemon is ALERTED (stale) when heartbeat age > this value.
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


class DaemonAlert(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int
    alert_level: str  # "critical", "warning", "info"


class DaemonHeartbeatAlertsResponse(BaseModel):
    alert_count: int
    critical_count: int
    warning_count: int
    alerts: List[DaemonAlert]


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
    """Parse ISO-8601 timestamp; handle Z suffix; return naive UTC."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _alert_level(age_seconds: float, threshold: int) -> str:
    """Determine alert level based on how stale the heartbeat is."""
    ratio = age_seconds / threshold if threshold > 0 else float("inf")
    if ratio >= 5.0:
        return "critical"
    elif ratio >= 2.0:
        return "warning"
    return "info"


@router.get("/daemons/alerts", response_model=DaemonHeartbeatAlertsResponse)
def daemon_heartbeat_alerts() -> DaemonHeartbeatAlertsResponse:
    """
    Return all daemons whose last heartbeat is stale (age > threshold).
    Each alert is tagged with an alert level: critical (>=5x threshold),
    warning (>=2x threshold), or info (just over threshold).
    """
    rows = _query_service_health()
    # Use naive UTC now (matches _parse_timestamp output)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    alerts: List[DaemonAlert] = []
    critical_count = 0
    warning_count = 0

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
            level = _alert_level(age_seconds, threshold)
            if level == "critical":
                critical_count += 1
            elif level == "warning":
                warning_count += 1

            alerts.append(DaemonAlert(
                name=name,
                last_heartbeat=raw_ts,
                age_seconds=age_seconds,
                status=status,
                threshold_seconds=threshold,
                alert_level=level,
            ))

    return DaemonHeartbeatAlertsResponse(
        alert_count=len(alerts),
        critical_count=critical_count,
        warning_count=warning_count,
        alerts=alerts,
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

    # Build timestamps using timedelta to avoid edge-case datetime arithmetic
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    recent = now_utc - timedelta(seconds=5)    # barely stale for fast daemons
    stale_info = now_utc - timedelta(seconds=500)  # ~500s ago → 1.67x default 300s → info
    stale_critical = now_utc - timedelta(seconds=2000)  # ~2000s ago → 16.7x signal_analyser 120s → critical
    _ts_fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")

    _test_rows = [
        # age ~5s, threshold 14400s → NOT stale
        {"service": "gate_orchestrator", "status": "ok", "last_heartbeat": _ts_fmt(recent)},
        # age ~5s, threshold 120s → NOT stale
        {"service": "signal_analyser", "status": "degraded", "last_heartbeat": _ts_fmt(recent)},
        # age ~0s, threshold 300s → NOT stale
        {"service": "write_service", "status": "healthy", "last_heartbeat": _ts_fmt(now_utc)},
        # age ~500s, default 300s → STALE, info level (ratio ~1.67x)
        {"service": "unknown_daemon", "status": "unknown", "last_heartbeat": _ts_fmt(stale_info)},
        # age ~2000s, threshold 120s → STALE, critical level (ratio ~16.7x)
        {"service": "signal_analyser", "status": "down", "last_heartbeat": _ts_fmt(stale_critical)},
    ]

    def _fake_post(url, json, timeout):
        return _FakeResponse({"rows": _test_rows})

    _original_post = requests.post
    requests.post = _fake_post

    try:
        client = TestClient(app)
        response = client.get("/api/daemons/alerts")
        assert response.status_code == 200, f"Unexpected status: {response.status_code}"

        data = response.json()
        assert "alert_count" in data, "Missing 'alert_count' key"
        assert "critical_count" in data, "Missing 'critical_count' key"
        assert "warning_count" in data, "Missing 'warning_count' key"
        assert "alerts" in data, "Missing 'alerts' key"

        # unknown_daemon: 500s / 300s = 1.67x → info (not counted in critical/warning)
        # signal_analyser (down): 2000s / 120s = 16.7x → critical (>=5x)
        assert data["alert_count"] == 2, f"alert_count mismatch: {data['alert_count']}"
        assert data["critical_count"] == 1, f"critical_count mismatch: {data['critical_count']}"
        assert data["warning_count"] == 0, f"warning_count mismatch: {data['warning_count']}"

        alert_names = {a["name"] for a in data["alerts"]}
        assert "unknown_daemon" in alert_names, f"unknown_daemon should be in alerts: {alert_names}"
        assert "signal_analyser" in alert_names, f"signal_analyser should be in alerts: {alert_names}"
        assert "gate_orchestrator" not in alert_names, f"gate_orchestrator should NOT be in alerts: {alert_names}"
        assert "write_service" not in alert_names, f"write_service should NOT be in alerts: {alert_names}"

        # Verify alert levels
        levels = {a["name"]: a["alert_level"] for a in data["alerts"]}
        assert levels.get("signal_analyser") == "critical", f"signal_analyser should be critical: {levels.get('signal_analyser')}"
        assert levels.get("unknown_daemon") == "info", f"unknown_daemon should be info: {levels.get('unknown_daemon')}"

        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = _original_post

# services/staged/daemon_liveness_api/logic.py
from __future__ import annotations

import datetime
from datetime import datetime as dt, timedelta
from typing import List, Dict, Any

import requests
from fastapi import Depends
from pydantic import BaseModel

# Real data layer import (session not used directly here but required by contract)
from app.db import get_session  # noqa: F401


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class DaemonInfo(BaseModel):
    name: str
    status: str
    last_heartbeat_iso: str
    age_seconds: int
    stale: bool
    threshold_seconds: int


class DaemonLivenessResponse(BaseModel):
    daemons: List[DaemonInfo]


# --------------------------------------------------------------------------- #
# Helper: query the write‑service for service health rows
# --------------------------------------------------------------------------- #
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _query_service_health() -> List[Dict[str, Any]]:
    """
    Query the write‑service for the `service_health` table.
    Expected return format (as produced by the write‑service):
        {"rows": [{...}, {...}, ...]}
    """
    sql = "SELECT service_name, status, last_heartbeat, meta FROM service_health"
    resp = requests.post(_WRITE_SERVICE_URL, json={"sql": sql})
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


# --------------------------------------------------------------------------- #
# Threshold configuration
# --------------------------------------------------------------------------- #
_DEFAULT_THRESHOLD = 900
_THRESHOLD_OVERRIDES: Dict[str, int] = {
    # ≤300 s
    "pipeline_bridge": 300,
    "trust_synthesiser": 300,
    "risk_ranker": 300,
    "write_service": 300,
    "manager_agent": 300,
    # ≤600 s
    "inference_router": 600,
    "mcp_scanner": 600,
    "signal_analyser": 600,
}


def _threshold_for(name: str) -> int:
    return _THRESHOLD_OVERRIDES.get(name, _DEFAULT_THRESHOLD)


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def get_daemon_liveness(session=Depends(get_session)) -> DaemonLivenessResponse:  # noqa: B008
    """
    Retrieve daemon health information, compute age and staleness.
    """
    rows = _query_service_health()
    now = dt.utcnow()

    daemon_infos: List[DaemonInfo] = []
    for row in rows:
        name: str = row.get("service_name", "")
        status: str = row.get("status", "")
        last_hb_raw = row.get("last_heartbeat")
        # The write‑service returns ISO‑8601 strings; fall back to now if missing/invalid
        try:
            last_hb_dt = dt.fromisoformat(last_hb_raw) if isinstance(last_hb_raw, str) else now
        except Exception:
            last_hb_dt = now
        age_seconds = int((now - last_hb_dt).total_seconds())
        threshold = _threshold_for(name)
        stale = age_seconds > threshold

        daemon_infos.append(
            DaemonInfo(
                name=name,
                status=status,
                last_heartbeat_iso=last_hb_dt.isoformat(),
                age_seconds=age_seconds,
                stale=stale,
                threshold_seconds=threshold,
            )
        )

    return DaemonLivenessResponse(daemons=daemon_infos)


# --------------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Mock write‑service response
    # ------------------------------------------------------------------- #
    _original_post = requests.post

    def _mock_post(url, json):
        assert url == _WRITE_SERVICE_URL
        now = dt.utcnow()
        rows = [
            {
                "service_name": "write_service",
                "status": "down",
                "last_heartbeat": (now - timedelta(seconds=400)).isoformat(),
                "meta": {},
            },
            {
                "service_name": "rug_pull_monitor",
                "status": "down",
                "last_heartbeat": (now - timedelta(seconds=1000)).isoformat(),
                "meta": {},
            },
            {
                "service_name": "inference_router",
                "status": "up",
                "last_heartbeat": (now - timedelta(seconds=100)).isoformat(),
                "meta": {},
            },
            {
                "service_name": "mcp_scanner",
                "status": "up",
                "last_heartbeat": (now - timedelta(seconds=200)).isoformat(),
                "meta": {},
            },
        ]
        class _Resp:
            def raise_for_status(self):  # pragma: no cover
                pass

            def json(self):
                return {"rows": rows}

        return _Resp()

    requests.post = _mock_post

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()

    @app.get(
        "/api/health/daemons",
        response_model=DaemonLivenessResponse,
        tags=["health"],
    )
    def health_endpoint():
        return get_daemon_liveness()

    # Override the DB session dependency with a dummy (not used)
    app.dependency_overrides[get_session] = lambda: None

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test
    # ------------------------------------------------------------------- #
    resp = client.get("/api/health/daemons")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    daemons = payload.get("daemons", [])
    assert len(daemons) == 4, f"Expected 4 daemons, got {len(daemons)}"

    # Helper to find daemon by name
    def _find(name: str) -> Dict[str, Any]:
        for d in daemons:
            if d["name"] == name:
                return d
        raise AssertionError(f"Daemon {name} not found")

    assert _find("write_service")["stale"] is True, "write_service should be stale"
    assert _find("rug_pull_monitor")["stale"] is True, "rug_pull_monitor should be stale"
    assert _find("inference_router")["stale"] is False, "inference_router should be healthy"
    assert _find("mcp_scanner")["stale"] is False, "mcp_scanner should be healthy"

    # Restore original requests.post
    requests.post = _original_post

    print("PASS")
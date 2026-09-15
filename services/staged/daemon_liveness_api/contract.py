"""
services/staged/daemon_liveness_api/contract.py

FastAPI contract for daemon liveness health endpoint.
Mirrors the exemplar contract implementation.
"""

from __future__ import annotations

import datetime
from datetime import timezone
from typing import List

import requests
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

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
# Helper utilities
# --------------------------------------------------------------------------- #

# per‑daemon thresholds (seconds)
_PER_DAEMON_THRESHOLDS = {
    # <= 300 s
    "pipeline_bridge": 300,
    "trust_synthesiser": 300,
    "risk_ranker": 300,
    "write_service": 300,
    "manager_agent": 300,
    # <= 600 s
    "inference_router": 600,
    "mcp_scanner": 600,
    "signal_analyser": 600,
}


def _threshold_for(name: str) -> int:
    """Return the stale‑threshold for a daemon name."""
    return _PER_DAEMON_THRESHOLDS.get(name, 900)  # default 900 s


def _fetch_service_health() -> List[dict]:
    """
    Query the write‑service bus for the ``service_health`` table.

    Returns a list of dicts with keys:
        service_name, status, last_heartbeat, meta
    """
    payload = {
        "query": "SELECT service_name, status, last_heartbeat, meta FROM service_health"
    }
    resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def _build_response(rows: List[dict]) -> DaemonLivenessResponse:
    now = datetime.datetime.now(timezone.utc)
    daemons: List[DaemonInfo] = []

    for row in rows:
        name: str = row["service_name"]
        status: str = row["status"]
        # ``last_heartbeat`` is expected to be an ISO‑8601 string with timezone info
        last_hb_iso: str = row["last_heartbeat"]
        try:
            last_hb_dt = datetime.datetime.fromisoformat(last_hb_iso)
        except Exception:
            # fallback – treat as naive UTC
            last_hb_dt = datetime.datetime.fromisoformat(last_hb_iso).replace(tzinfo=timezone.utc)

        age_seconds = int((now - last_hb_dt).total_seconds())
        threshold = _threshold_for(name)
        stale = age_seconds > threshold

        daemons.append(
            DaemonInfo(
                name=name,
                status=status,
                last_heartbeat_iso=last_hb_iso,
                age_seconds=age_seconds,
                stale=stale,
                threshold_seconds=threshold,
            )
        )

    return DaemonLivenessResponse(daemons=daemons)


# --------------------------------------------------------------------------- #
# FastAPI router / app
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/api")


@router.get("/health/daemons", response_model=DaemonLivenessResponse)
def get_daemon_liveness() -> DaemonLivenessResponse:
    rows = _fetch_service_health()
    return _build_response(rows)


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (acceptance)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    # Seed data for the acceptance test
    now = datetime.datetime.now(timezone.utc)

    seed_rows = [
        {
            "service_name": "write_service",
            "status": "up",
            "last_heartbeat": (now - datetime.timedelta(seconds=400)).isoformat(),
            "meta": {},
        },
        {
            "service_name": "rug_pull_monitor",
            "status": "up",
            "last_heartbeat": (now - datetime.timedelta(seconds=1000)).isoformat(),
            "meta": {},
        },
        {
            "service_name": "inference_router",
            "status": "up",
            "last_heartbeat": (now - datetime.timedelta(seconds=100)).isoformat(),
            "meta": {},
        },
        {
            "service_name": "mcp_scanner",
            "status": "up",
            "last_heartbeat": (now - datetime.timedelta(seconds=200)).isoformat(),
            "meta": {},
        },
    ]

    # Mock ``requests.post`` used inside ``_fetch_service_health``
    class _MockResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def _mock_post(url, json, timeout):
        # Ensure the correct endpoint is being called (optional sanity check)
        if url != "http://127.0.0.1:8772/query":
            raise RuntimeError(f"Unexpected URL: {url}")
        return _MockResponse(seed_rows)

    with patch("requests.post", new=_mock_post):
        client = TestClient(app)
        resp = client.get("/api/health/daemons")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        daemons = data.get("daemons", [])
        assert len(daemons) == 4, f"Expected 4 daemons, got {len(daemons)}"

        # Helper to find daemon entry by name
        def _find(name: str) -> dict:
            for d in daemons:
                if d["name"] == name:
                    return d
            raise AssertionError(f"Daemon {name!r} not found")

        # Assertions on stale flags
        assert _find("write_service")["stale"] is True, "write_service should be stale"
        assert _find("rug_pull_monitor")["stale"] is True, "rug_pull_monitor should be stale"
        assert _find("inference_router")["stale"] is False, "inference_router should be fresh"
        assert _find("mcp_scanner")["stale"] is False, "mcp_scanner should be fresh"

    print("PASS")
    sys.exit(0)
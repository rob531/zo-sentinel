# deps: fastapi, pydantic, requests
"""FastAPI router for wisdom_synthesiser health status.

Queries the write_service service_health table (DuckDB store) to return
health status for the wisdom_synthesiser daemon. Public access, no auth required.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["wisdom_synthesiser_health"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Stale threshold (seconds) for wisdom_synthesiser (4h).
THRESHOLD_SECONDS = 14400


class WisdomSynthesiserHealthResponse(BaseModel):
    service: str
    last_heartbeat: Optional[str]
    age_seconds: Optional[float]
    status: Optional[str]
    threshold_seconds: int
    is_stale: bool
    meta: Optional[dict] = None


def _query_service_health(service: str) -> Optional[dict]:
    """Query write_service service_health table for a specific service."""
    payload = {
        "sql": f"SELECT service, status, last_heartbeat, meta FROM {SERVICE_HEALTH_TABLE} WHERE service = ?",
        "params": [service],
    }
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to query write_service: {exc}")
    data = resp.json()
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail="Malformed response from write_service")
    return rows[0] if rows else None


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp; handle Z suffix."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/wisdom-synthesiser/health", response_model=WisdomSynthesiserHealthResponse)
def wisdom_synthesiser_health() -> WisdomSynthesiserHealthResponse:
    """Return health status for the wisdom_synthesiser daemon."""
    row = _query_service_health("wisdom_synthesiser")

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="wisdom_synthesiser not found in service_health",
        )

    raw_ts = row.get("last_heartbeat")
    status = row.get("status")

    if not raw_ts:
        return WisdomSynthesiserHealthResponse(
            service="wisdom_synthesiser",
            last_heartbeat=None,
            age_seconds=None,
            status=status,
            threshold_seconds=THRESHOLD_SECONDS,
            is_stale=True,
            meta=row.get("meta"),
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        hb = _parse_timestamp(raw_ts)
    except Exception:
        hb = now

    age_seconds = (now - hb).total_seconds()
    is_stale = age_seconds > THRESHOLD_SECONDS

    return WisdomSynthesiserHealthResponse(
        service="wisdom_synthesiser",
        last_heartbeat=raw_ts,
        age_seconds=age_seconds,
        status=status,
        threshold_seconds=THRESHOLD_SECONDS,
        is_stale=is_stale,
        meta=row.get("meta"),
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

    _now_ts = datetime.now(timezone.utc)
    _recent = _now_ts - __import__("datetime").timedelta(seconds=5)
    _old = _now_ts - __import__("datetime").timedelta(seconds=16000)  # exceeds 14400 threshold
    _ts_fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")

    def _fake_post_stale(url, json, timeout):
        if "WHERE service = ?" in json.get("sql", ""):
            svc = json["params"][0]
            if svc == "wisdom_synthesiser":
                return _FakeResponse({"rows": [
                    {"service": "wisdom_synthesiser", "status": "ok",
                     "last_heartbeat": _ts_fmt(_old),
                     "meta": {"version": "1.2.0"}}
                ]})
        return _FakeResponse({"rows": []})

    _original_post = requests.post
    requests.post = _fake_post_stale

    try:
        client = TestClient(app)

        # Case 1: stale heartbeat
        response = client.get("/api/wisdom-synthesiser/health")
        assert response.status_code == 200, f"Unexpected status: {response.status_code}"
        data = response.json()
        assert data["service"] == "wisdom_synthesiser"
        assert data["is_stale"] is True, f"Expected stale=True (age ~500s > 14400s threshold)"
        assert data["threshold_seconds"] == 14400
        assert data["status"] == "ok"
        assert data["meta"] == {"version": "1.2.0"}

        # Case 2: not-stale heartbeat
        def _fake_post_healthy(url, json, timeout):
            if "WHERE service = ?" in json.get("sql", ""):
                svc = json["params"][0]
                if svc == "wisdom_synthesiser":
                    return _FakeResponse({"rows": [
                        {"service": "wisdom_synthesiser", "status": "healthy",
                         "last_heartbeat": _ts_fmt(_recent), "meta": None}
                    ]})
            return _FakeResponse({"rows": []})

        requests.post = _fake_post_healthy
        response2 = client.get("/api/wisdom-synthesiser/health")
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["is_stale"] is False, f"Expected stale=False (age ~5s < 14400s)"

        # Case 3: not found
        def _fake_post_missing(url, json, timeout):
            return _FakeResponse({"rows": []})

        requests.post = _fake_post_missing
        response3 = client.get("/api/wisdom-synthesiser/health")
        assert response3.status_code == 404, f"Expected 404, got {response3.status_code}"

        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = _original_post

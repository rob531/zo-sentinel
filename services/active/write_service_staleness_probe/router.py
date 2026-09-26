# deps: fastapi, pydantic, requests
"""Write Service Staleness Probe.

Monitors write_service daemon health by checking:
  - Responsiveness via HTTP query call.
  - Heartbeat age from service_health table.
  - Recent restart events.
  - Service registry consistency.

Auth: public.  Prefix: /api.  Tag: write_service_staleness_probe.
Data: write_service DuckDB via POST http://127.0.0.1:8772/query.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

router = APIRouter(prefix="/api", tags=["write_service_staleness_probe"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 10
HEARTBEAT_STALE_THRESHOLD_SECS = 300  # 5 minutes


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class WriteServiceResponsiveCheck(BaseModel):
    responsive: bool = Field(..., description="Whether write_service answered the probe query")
    latency_ms: float = Field(..., description="Round-trip latency in milliseconds")
    error: Optional[str] = Field(None, description="Error message if unresponsive")


class WriteServiceHeartbeatAge(BaseModel):
    service: str = Field(..., description="Service name")
    last_heartbeat: str = Field(..., description="ISO-8601 timestamp of last heartbeat")
    age_seconds: float = Field(..., description="Age of heartbeat in seconds")
    is_stale: bool = Field(..., description="True if age exceeds threshold")


class WriteServiceRegistryCheck(BaseModel):
    total_servers: int = Field(..., description="Total servers in registry")
    healthy_count: int = Field(..., description="Servers with trust_score > 0")
    stale_count: int = Field(..., description="Servers with no trust_score")


class WriteServiceRestartCheck(BaseModel):
    restarted_recently: bool = Field(..., description="True if write_service appears to have restarted in the last hour")
    restart_candidates: List[str] = Field(
        default_factory=list,
        description="Service names that look like restarts",
    )


class WriteServiceStalenessResponse(BaseModel):
    generated_at: datetime = Field(..., description="ISO-8601 timestamp of report generation")
    responsive: WriteServiceResponsiveCheck
    heartbeat_age: WriteServiceHeartbeatAge
    registry: WriteServiceRegistryCheck
    restarts: WriteServiceRestartCheck
    overall_status: str = Field(..., description="'healthy', 'degraded', or 'unhealthy'")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_ts(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, handling Z suffix."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def ws_query(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute a SELECT query against write_service DuckDB."""
    payload: Dict[str, Any] = {"sql": sql, "params": params or []}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def check_write_service_responsive() -> WriteServiceResponsiveCheck:
    """Send a lightweight probe query and measure latency."""
    try:
        before = datetime.now(timezone.utc)
        result = ws_query("SELECT 1 AS ping")
        after = datetime.now(timezone.utc)
        latency_ms = (after - before).total_seconds() * 1000.0
        rows = result.get("rows", [])
        if isinstance(rows, list) and len(rows) > 0 and rows[0].get("ping") == 1:
            return WriteServiceResponsiveCheck(responsive=True, latency_ms=round(latency_ms, 2))
        return WriteServiceResponsiveCheck(
            responsive=False,
            latency_ms=round(latency_ms, 2),
            error="Unexpected response shape",
        )
    except requests.RequestException as exc:
        return WriteServiceResponsiveCheck(responsive=False, latency_ms=0.0, error=str(exc))
    except Exception as exc:
        return WriteServiceResponsiveCheck(responsive=False, latency_ms=0.0, error=str(exc))


def get_write_service_heartbeat_age() -> WriteServiceHeartbeatAge:
    """Query service_health for write_service last heartbeat."""
    try:
        result = ws_query(
            "SELECT service, status, last_heartbeat FROM service_health WHERE service = 'write_service'"
        )
        rows = result.get("rows", [])
        if not rows:
            # Write service row may be missing -- treat as stale
            return WriteServiceHeartbeatAge(
                service="write_service",
                last_heartbeat="",
                age_seconds=float("inf"),
                is_stale=True,
            )
        row = rows[0]
        raw_ts = row.get("last_heartbeat", "")
        if not raw_ts:
            return WriteServiceHeartbeatAge(
                service="write_service",
                last_heartbeat="",
                age_seconds=float("inf"),
                is_stale=True,
            )
        hb_ts = _parse_ts(raw_ts)
        now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
        age_seconds = (now_ts - hb_ts).total_seconds()
        return WriteServiceHeartbeatAge(
            service="write_service",
            last_heartbeat=raw_ts,
            age_seconds=round(age_seconds, 1),
            is_stale=age_seconds > HEARTBEAT_STALE_THRESHOLD_SECS,
        )
    except requests.RequestException as exc:
        return WriteServiceHeartbeatAge(
            service="write_service",
            last_heartbeat="",
            age_seconds=float("inf"),
            is_stale=True,
        )
    except Exception as exc:
        return WriteServiceHeartbeatAge(
            service="write_service",
            last_heartbeat="",
            age_seconds=float("inf"),
            is_stale=True,
        )


def check_service_registry() -> WriteServiceRegistryCheck:
    """Query write_service for total / healthy server counts."""
    try:
        result = ws_query(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN trust_score > 0 THEN 1 ELSE 0 END) AS healthy_count "
            "FROM mcp_server_registry"
        )
        rows = result.get("rows", [])
        if not rows:
            return WriteServiceRegistryCheck(total_servers=0, healthy_count=0, stale_count=0)
        row = rows[0]
        total = int(row.get("total", 0) or 0)
        healthy = int(row.get("healthy_count", 0) or 0)
        return WriteServiceRegistryCheck(
            total_servers=total,
            healthy_count=healthy,
            stale_count=total - healthy,
        )
    except requests.RequestException:
        return WriteServiceRegistryCheck(total_servers=0, healthy_count=0, stale_count=0)
    except Exception:
        return WriteServiceRegistryCheck(total_servers=0, healthy_count=0, stale_count=0)


def check_recent_restarts() -> WriteServiceRestartCheck:
    """Detect services that may have restarted recently by checking for
    multiple consecutive heartbeats within a short window (heuristic)."""
    try:
        result = ws_query(
            "SELECT service, status, last_heartbeat, meta FROM service_health "
            "ORDER BY last_heartbeat DESC LIMIT 50"
        )
        rows = result.get("rows", [])
        restart_candidates: List[str] = []

        # Group by service, look for timestamp discontinuities
        services_seen: Dict[str, List[datetime]] = {}
        for row in rows:
            svc = row.get("service", "")
            raw_ts = row.get("last_heartbeat", "")
            if not svc or not raw_ts:
                continue
            try:
                ts = _parse_ts(raw_ts)
                services_seen.setdefault(svc, []).append(ts)
            except Exception:
                continue

        for svc, timestamps in services_seen.items():
            if len(timestamps) < 2:
                continue
            # If latest heartbeat is very recent and there's a gap suggesting
            # a restart, flag it.
            timestamps_sorted = sorted(timestamps, reverse=True)
            latest = timestamps_sorted[0]
            now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
            latest_age = (now_ts - latest).total_seconds()
            if latest_age < 60 and len(timestamps_sorted) > 1:
                # Latest is within the last minute -- check if previous is much older
                prev = timestamps_sorted[1]
                gap = (latest - prev).total_seconds()
                if gap > HEARTBEAT_STALE_THRESHOLD_SECS:
                    restart_candidates.append(svc)

        return WriteServiceRestartCheck(
            restarted_recently=len(restart_candidates) > 0,
            restart_candidates=restart_candidates[:10],
        )
    except requests.RequestException:
        return WriteServiceRestartCheck(restarted_recently=False, restart_candidates=[])
    except Exception:
        return WriteServiceRestartCheck(restarted_recently=False, restart_candidates=[])


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/write-service/staleness",
    response_model=WriteServiceStalenessResponse,
    name="write_service_staleness_probe:get",
)
def get_write_service_staleness() -> WriteServiceStalenessResponse:
    """
    Return a composite staleness health report for write_service:
    - HTTP responsiveness and latency
    - Heartbeat age vs 5-minute threshold
    - Registry stats (total / healthy / stale servers)
    - Detected restart candidates
    """
    responsive = check_write_service_responsive()
    hb_age = get_write_service_heartbeat_age()
    registry = check_service_registry()
    restarts = check_recent_restarts()

    if not responsive.responsive or hb_age.is_stale:
        overall = "unhealthy"
    elif registry.total_servers == 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return WriteServiceStalenessResponse(
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        responsive=responsive,
        heartbeat_age=hb_age,
        registry=registry,
        restarts=restarts,
        overall_status=overall,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import types
    from unittest.mock import patch

    # Build a minimal stub for app so imports don't crash
    _stub_app = types.ModuleType("app")
    _stub_db = types.ModuleType("app.db")
    _stub_models = types.ModuleType("app.models")
    sys.modules["app"] = _stub_app
    sys.modules["app.db"] = _stub_db
    sys.modules["app.models"] = _stub_models

    _stub_db.get_session = object  # not used by this service

    test_app = FastAPI()
    test_app.include_router(router)

    # Fake write_service responses
    _write_service_responses: List[Dict[str, Any]] = []
    _query_index = 0

    def _fake_post(url: str, json: Dict[str, Any], **kwargs: Any) -> types.SimpleNamespace:
        resp = types.SimpleNamespace()

        if "/query" in url:
            sql = json.get("sql", "")

            if sql.strip().upper().startswith("SELECT 1"):
                resp.status_code = 200
                resp.json = lambda: {"rows": [{"ping": 1}]}
            elif "service_health" in sql and "WHERE service = 'write_service'" in sql:
                now = datetime.now(timezone.utc).isoformat()
                resp.status_code = 200
                resp.json = lambda: {"rows": [{"service": "write_service", "status": "ok", "last_heartbeat": now}]}
            elif "service_health" in sql and "ORDER BY" in sql:
                now = datetime.now(timezone.utc).isoformat()
                older = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
                resp.status_code = 200
                resp.json = lambda: {
                    "rows": [
                        {"service": "write_service", "status": "ok", "last_heartbeat": now, "meta": None},
                        {"service": "write_service", "status": "ok", "last_heartbeat": older, "meta": None},
                    ]
                }
            elif "mcp_server_registry" in sql:
                resp.status_code = 200
                resp.json = lambda: {"rows": [{"total": 10, "healthy_count": 8}]}
            else:
                resp.status_code = 200
                resp.json = lambda: {"rows": []}

            def raise_for_status() -> None:
                if resp.status_code >= 400:
                    raise requests.HTTPError("HTTP Error", response=resp)

            resp.raise_for_status = raise_for_status
        else:
            resp.status_code = 200
            resp.json = lambda: {}
            resp.raise_for_status = lambda: None

        return resp

    with patch("requests.post", side_effect=_fake_post):
        client = TestClient(test_app)

        # Test: healthy write_service
        resp = client.get("/api/write-service/staleness")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["overall_status"] in ("healthy", "degraded", "unhealthy"), f"invalid status: {data['overall_status']}"
        assert "responsive" in data
        assert "heartbeat_age" in data
        assert "registry" in data
        assert "restarts" in data
        assert data["responsive"]["responsive"] is True
        assert data["registry"]["total_servers"] == 10
        assert data["registry"]["healthy_count"] == 8

        # Test: unresponsive write_service
        def _fake_unresponsive(url: str, **kwargs: Any) -> types.SimpleNamespace:
            raise requests.ConnectionError("Connection refused")

        with patch("requests.post", side_effect=_fake_unresponsive):
            resp2 = client.get("/api/write-service/staleness")
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["overall_status"] == "unhealthy"
            assert data2["responsive"]["responsive"] is False
            assert "Connection refused" in (data2["responsive"].get("error") or "")

    print("PASS")

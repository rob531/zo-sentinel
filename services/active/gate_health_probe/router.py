# deps: fastapi, pydantic, sqlalchemy, requests
"""Gate Health Probe Service.

Provides health-check and diagnostic endpoints for the gate system.
Public access (no auth). Reads from app Postgres via get_session + models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["gate_health_probe"])


# --- Response models --------------------------------------------------------

class ServiceHealth(BaseModel):
    service: str
    status: str
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class GateHealthResponse(BaseModel):
    healthy: bool
    timestamp: str
    checks: List[ServiceHealth]
    summary: str


class GateStatsResponse(BaseModel):
    total_servers: int
    total_scores: int
    servers_scored: int
    servers_unscored: int
    last_score_at: Optional[str]


# --- Helper -----------------------------------------------------------------

def _ping_service(url: str, timeout: float = 2.0) -> tuple[str, Optional[float], Optional[str]]:
    """Ping an HTTP endpoint; return (status, latency_ms, detail)."""
    try:
        start = datetime.now(timezone.utc)
        resp = requests.get(url, timeout=timeout)
        latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        if resp.status_code < 500:
            return "ok", latency, None
        return "degraded", latency, f"HTTP {resp.status_code}"
    except requests.RequestException as e:
        return "unreachable", None, str(e)[:80]


# --- Endpoints --------------------------------------------------------------

@router.get("/gate_health", response_model=GateHealthResponse)
def gate_health(db: Session = Depends(get_session)) -> GateHealthResponse:
    """Aggregate health check across gate subsystems."""
    now = datetime.now(timezone.utc).isoformat()
    checks: list[ServiceHealth] = []

    # 1. App DB connectivity
    status_db, lat_db, detail_db = "unreachable", None, None
    try:
        start = datetime.now(timezone.utc)
        db.execute(text("SELECT 1"))
        lat_db = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        status_db = "ok"
    except Exception as e:
        detail_db = str(e)[:80]
    checks.append(ServiceHealth(service="app_db", status=status_db, latency_ms=lat_db, detail=detail_db))

    # 2. Mesh/write_service connectivity
    status_mesh, lat_mesh, detail_mesh = _ping_service("http://127.0.0.1:8772/health", timeout=3.0)
    checks.append(ServiceHealth(service="mesh_write_service", status=status_mesh, latency_ms=lat_mesh, detail=detail_mesh))

    # 3. Schema consistency: count registry vs scores
    status_schema, detail_schema = "ok", None
    try:
        reg_count = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
        score_count = db.query(func.count(McpLlmAxisScore.id)).scalar() or 0
        if reg_count == 0 and score_count == 0:
            status_schema = "empty"
            detail_schema = "No data in registry or scores"
        elif reg_count > 0 and score_count == 0:
            status_schema = "degraded"
            detail_schema = f"{reg_count} servers but 0 scores"
    except Exception as e:
        status_schema = "error"
        detail_schema = str(e)[:80]
    checks.append(ServiceHealth(service="schema_consistency", status=status_schema, detail=detail_schema))

    healthy = all(c.status in ("ok", "empty") for c in checks)
    summary = "healthy" if healthy else "one or more checks failed"
    return GateHealthResponse(healthy=healthy, timestamp=now, checks=checks, summary=summary)


@router.get("/gate_stats", response_model=GateStatsResponse)
def gate_stats(db: Session = Depends(get_session)) -> GateStatsResponse:
    """Gate scoring statistics snapshot."""
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    total_scores = db.query(func.count(McpLlmAxisScore.id)).scalar() or 0
    scored = db.query(func.count(func.distinct(McpLlmAxisScore.server_id))).scalar() or 0
    servers_unscored = max(0, total_servers - scored)

    last_score_row = (
        db.query(McpLlmAxisScore.scored_at)
        .filter(McpLlmAxisScore.scored_at.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    last_score_at = last_score_row[0].isoformat() if last_score_row else None

    return GateStatsResponse(
        total_servers=total_servers,
        total_scores=total_scores,
        servers_scored=scored,
        servers_unscored=servers_unscored,
        last_score_at=last_score_at,
    )


# --- Self-test --------------------------------------------------------------
if __name__ == "__main__":
    import sqlite3, sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test_gate_health.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test gate_health
    r = client.get("/api/gate_health")
    assert r.status_code == 200, f"gate_health failed: {r.text}"
    data = r.json()
    assert "healthy" in data
    assert "checks" in data
    assert "timestamp" in data
    print(f"gate_health OK: healthy={data['healthy']}, checks={[c['service'] for c in data['checks']]}")

    # Test gate_stats
    r = client.get("/api/gate_stats")
    assert r.status_code == 200, f"gate_stats failed: {r.text}"
    data = r.json()
    assert "total_servers" in data
    assert "total_scores" in data
    assert "servers_scored" in data
    assert "servers_unscored" in data
    print(f"gate_stats OK: total_servers={data['total_servers']}, total_scores={data['total_scores']}")

    # Cleanup
    import os
    os.remove("./test_gate_health.db")

    print("PASS")
    sys.exit(0)

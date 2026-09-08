# deps: fastapi, pydantic, sqlalchemy, requests
"""Scoring Health Probe Service.

Public health/diagnostic endpoints for the scoring subsystem.
Reads app Postgres via get_session + ORM models.
"""
from __future__ import annotations

import requests
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_health_probe"])


# --- Response models --------------------------------------------------------

class SubsystemCheck(BaseModel):
    subsystem: str
    status: str  # ok | degraded | unreachable | error
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class ScoringHealthResponse(BaseModel):
    healthy: bool
    timestamp: str
    checks: List[SubsystemCheck]
    summary: str


class ScoringStatsResponse(BaseModel):
    total_servers: int
    total_scores: int
    servers_scored: int
    servers_unscored: int
    last_score_at: Optional[str]


# --- Helper -----------------------------------------------------------------

def _http_check(url: str, timeout: float = 3.0) -> tuple[str, Optional[float], Optional[str]]:
    """Return (status, latency_ms, detail) for an HTTP GET."""
    try:
        start = datetime.now(timezone.utc)
        resp = requests.get(url, timeout=timeout)
        latency_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        if resp.status_code < 500:
            return "ok", latency_ms, None
        return "degraded", latency_ms, f"HTTP {resp.status_code}"
    except requests.RequestException as e:
        return "unreachable", None, str(e)[:80]


# --- Endpoints --------------------------------------------------------------

@router.get("/scoring_health_probe/health", response_model=ScoringHealthResponse)
def scoring_health_probe(db: Session = Depends(get_session)) -> ScoringHealthResponse:
    """Aggregate health check across scoring subsystems."""
    now = datetime.now(timezone.utc).isoformat()
    checks: list[SubsystemCheck] = []

    # 1. App DB connectivity
    status_db, lat_db, detail_db = "unreachable", None, None
    try:
        start = datetime.now(timezone.utc)
        db.execute(text("SELECT 1"))
        lat_db = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        status_db = "ok"
    except Exception as e:
        detail_db = str(e)[:80]
    checks.append(SubsystemCheck(subsystem="app_db", status=status_db, latency_ms=lat_db, detail=detail_db))

    # 2. Mesh write_service
    status_mesh, lat_mesh, detail_mesh = _http_check("http://127.0.0.1:8772/health", timeout=3.0)
    checks.append(SubsystemCheck(subsystem="mesh_write_service", status=status_mesh, latency_ms=lat_mesh, detail=detail_mesh))

    # 3. Schema consistency: registry vs scores
    status_schema, detail_schema = "ok", None
    try:
        reg_count = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
        score_count = db.query(func.count(McpLlmAxisScore.id)).scalar() or 0
        if reg_count == 0 and score_count == 0:
            status_schema = "degraded"
            detail_schema = "No data in registry or scores"
        elif reg_count > 0 and score_count == 0:
            status_schema = "degraded"
            detail_schema = f"{reg_count} servers but 0 scores"
    except Exception as e:
        status_schema = "error"
        detail_schema = str(e)[:80]
    checks.append(SubsystemCheck(subsystem="schema_consistency", status=status_schema, detail=detail_schema))

    healthy = all(c.status in ("ok", "degraded") for c in checks)
    summary = "healthy" if healthy else "one or more checks failed"
    return ScoringHealthResponse(healthy=healthy, timestamp=now, checks=checks, summary=summary)


@router.get("/scoring_health_probe/stats", response_model=ScoringStatsResponse)
def scoring_stats(db: Session = Depends(get_session)) -> ScoringStatsResponse:
    """Scoring statistics snapshot."""
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    total_scores = db.query(func.count(McpLlmAxisScore.id)).scalar() or 0
    scored = db.query(func.count(func.distinct(McpLlmAxisScore.server_id))).scalar() or 0
    servers_unscored = max(0, total_servers - scored)

    last_row = (
        db.query(McpLlmAxisScore.scored_at)
        .filter(McpLlmAxisScore.scored_at.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    last_score_at = last_row[0].isoformat() if last_row else None

    return ScoringStatsResponse(
        total_servers=total_servers,
        total_scores=total_scores,
        servers_scored=scored,
        servers_unscored=servers_unscored,
        last_score_at=last_score_at,
    )


# --- Self-test --------------------------------------------------------------
if __name__ == "__main__":
    import sys, os

    # Ensure repo root is on the import path
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test health endpoint
    r = client.get("/api/scoring_health_probe/health")
    assert r.status_code == 200, f"health failed: {r.text}"
    data = r.json()
    assert "healthy" in data and "checks" in data and "timestamp" in data
    print(f"health OK: healthy={data['healthy']}, checks={[c['subsystem'] for c in data['checks']]}")

    # Test stats endpoint
    r = client.get("/api/scoring_health_probe/stats")
    assert r.status_code == 200, f"stats failed: {r.text}"
    data = r.json()
    for key in ("total_servers", "total_scores", "servers_scored", "servers_unscored"):
        assert key in data, f"missing key {key}"
    print(f"stats OK: servers={data['total_servers']}, scores={data['total_scores']}")

    print("PASS")
    sys.exit(0)

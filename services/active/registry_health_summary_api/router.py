# deps: fastapi, pydantic, sqlalchemy
"""Registry Health Summary API.

GET /api/registry/health-summary
  Returns aggregated health metrics over all servers in mcp_server_registry:
  total count, distribution by tier, distribution by verdict,
  average trust score, median days since last scan, never-scanned count,
  and high-risk server count.

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Generator

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_health_summary_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class HealthSummaryResponse(BaseModel):
    total_servers: int = Field(..., description="Total registered servers")
    by_tier: dict[str, int] = Field(..., description="Count per risk tier")
    by_verdict: dict[str, int] = Field(..., description="Count per verdict")
    avg_trust_score: float = Field(..., description="Average trust score across all servers")
    median_days_since_scan: float = Field(..., description="Median age of last scan in days")
    never_scanned: int = Field(..., description="Servers with scan_count == 0")
    high_risk_count: int = Field(..., description="Servers with HIGH_RISK_ISOLATED or KNOWN_THREAT verdict")

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

HIGH_RISK_VERDICTS = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}


def compute_health_summary(db: Session) -> HealthSummaryResponse:
    """Aggregate health metrics from mcp_server_registry."""
    now = datetime.now(timezone.utc)

    servers = db.query(McpServerRegistry).all()

    total_servers = len(servers)

    by_tier: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    trust_scores: list[float] = []
    scan_ages_days: list[float] = []
    never_scanned = 0
    high_risk_count = 0

    for s in servers:
        tier = s.risk_tier or "UNKNOWN"
        by_tier[tier] = by_tier.get(tier, 0) + 1

        verdict = s.verdict or "UNKNOWN"
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1

        if s.trust_score is not None:
            trust_scores.append(s.trust_score)

        if s.scan_count == 0:
            never_scanned += 1
        elif s.last_scanned:
            delta = now - s.last_scanned
            scan_ages_days.append(delta.total_seconds() / 86400.0)

        if verdict in HIGH_RISK_VERDICTS:
            high_risk_count += 1

    avg_trust_score = sum(trust_scores) / len(trust_scores) if trust_scores else 0.0
    median_days = median(scan_ages_days) if scan_ages_days else 0.0

    return HealthSummaryResponse(
        total_servers=total_servers,
        by_tier=by_tier,
        by_verdict=by_verdict,
        avg_trust_score=round(avg_trust_score, 4),
        median_days_since_scan=round(median_days, 4),
        never_scanned=never_scanned,
        high_risk_count=high_risk_count,
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get("/registry/health-summary", response_model=HealthSummaryResponse)
def health_summary(db: Session = Depends(get_session)) -> HealthSummaryResponse:
    """Return aggregated registry health metrics."""
    return compute_health_summary(db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    # Build a local FastAPI app for self-test
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite with StaticPool
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Seed test data
    now = datetime.now(timezone.utc)
    seed = [
        McpServerRegistry(
            server_id="srv-001", name="Srv 1", risk_tier="LOW", verdict="CLEAN",
            trust_score=0.85, scan_count=1, last_scanned=now,
            registry_source="test", url="http://srv1",
        ),
        McpServerRegistry(
            server_id="srv-002", name="Srv 2", risk_tier="MEDIUM", verdict="SUSPICIOUS",
            trust_score=0.55, scan_count=2, last_scanned=now,
            registry_source="test", url="http://srv2",
        ),
        McpServerRegistry(
            server_id="srv-003", name="Srv 3", risk_tier="LOW", verdict="CLEAN",
            trust_score=0.90, scan_count=0, last_scanned=None,
            registry_source="test", url="http://srv3",
        ),
        McpServerRegistry(
            server_id="srv-004", name="Srv 4", risk_tier="HIGH", verdict="HIGH_RISK_ISOLATED",
            trust_score=0.10, scan_count=3, last_scanned=now,
            registry_source="test", url="http://srv4",
        ),
        McpServerRegistry(
            server_id="srv-005", name="Srv 5", risk_tier="HIGH", verdict="KNOWN_THREAT",
            trust_score=0.05, scan_count=5, last_scanned=now,
            registry_source="test", url="http://srv5",
        ),
    ]

    with TestSession() as sess:
        for s in seed:
            sess.add(s)
        sess.commit()

    def _override() -> Generator[Session, None, None]:
        with TestSession() as s:
            yield s

    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)
    resp = client.get("/api/registry/health-summary")

    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()

    assert data["total_servers"] == 5, f"total_servers expected 5, got {data['total_servers']}"
    assert data["high_risk_count"] == 2, f"high_risk_count expected 2, got {data['high_risk_count']}"
    assert data["never_scanned"] == 1, f"never_scanned expected 1, got {data['never_scanned']}"
    assert "CLEAN" in data["by_verdict"], f"by_verdict missing CLEAN: {data['by_verdict']}"
    assert "LOW" in data["by_tier"], f"by_tier missing LOW: {data['by_tier']}"

    print("PASS")

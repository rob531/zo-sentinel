"""
services/staged/risk_overview_dashboard/contract.py

FastAPI contract for the risk overview dashboard.
Mirrors the structure of services/_exemplar/contract.py.
"""

from __future__ import annotations

import datetime
from collections import Counter
from typing import Dict, Optional

import uvicorn
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class DashboardOverview(BaseModel):
    total_servers: int = Field(..., description="Total number of registered servers")
    tier_distribution: Dict[str, int] = Field(
        ..., description="Count of servers per risk tier"
    )
    avg_trust_score: Optional[float] = Field(
        None, description="Average trust score across all servers"
    )
    recent_scans: int = Field(..., description="Number of recent LLM axis scores")
    updated_at: datetime.datetime = Field(..., description="Timestamp of response generation")


@router.get(
    "/dashboard/overview",
    response_model=DashboardOverview,
    tags=["dashboard"],
    summary="Risk overview dashboard summary",
)
def get_dashboard_overview(session: Session = Depends(get_session)):
    """
    Returns a high‑level overview of the risk dashboard.
    """

    # Total servers
    total_servers_stmt = select(func.count()).select_from(McpServerRegistry)
    total_servers = session.execute(total_servers_stmt).scalar_one()

    # Tier distribution
    tier_stmt = (
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .select_from(McpServerRegistry)
    )
    tier_rows = session.execute(tier_stmt).all()
    tier_distribution = {tier: count for tier, count in tier_rows}

    # Average trust score
    avg_trust_stmt = select(func.avg(McpServerRegistry.trust_score)).select_from(
        McpServerRegistry
    )
    avg_trust_score = session.execute(avg_trust_stmt).scalar_one()
    if avg_trust_score is not None:
        avg_trust_score = float(avg_trust_score)

    # Recent scans – count of LLM axis scores (no time filter for simplicity)
    recent_scans_stmt = select(func.count()).select_from(McpLlmAxisScore)
    recent_scans = session.execute(recent_scans_stmt).scalar_one()

    return DashboardOverview(
        total_servers=total_servers,
        tier_distribution=tier_distribution,
        avg_trust_score=avg_trust_score,
        recent_scans=recent_scans,
        updated_at=datetime.datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.risk_overview_dashboard.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test)
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite://"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        with TestSessionLocal() as session:
            yield session

    # ------------------------------------------------------------------- #
    # Seed test data (5 servers with mixed risk tiers)
    # ------------------------------------------------------------------- #
    tiers = ["low", "medium", "high", "critical"]
    with TestSessionLocal() as sess:
        for i in range(5):
            server = McpServerRegistry(
                server_id=f"test-srv-{i}",
                name=f"Test Server {i}",
                risk_tier=tiers[i % len(tiers)],
                trust_score=0.5 + i * 0.1,
                # optional fields left as None / defaults
            )
            sess.add(server)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute acceptance test
    # ------------------------------------------------------------------- #
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["total_servers"] == 5, "total_servers mismatch"
    assert isinstance(payload["tier_distribution"], dict), "tier_distribution not a dict"
    assert len(payload["tier_distribution"]) >= 2, "not enough tier keys"

    print("PASS")
    raise SystemExit(0)
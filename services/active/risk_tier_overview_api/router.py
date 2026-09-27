# services/active/risk_tier_overview_api/router.py
"""
Risk Tier Overview API - provides aggregate distribution of MCP servers across risk tiers.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_overview_api"])


class TierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float


class RiskTierOverviewResponse(BaseModel):
    total_servers: int
    tiers: List[TierDistribution]


@router.get("/risk/overview", response_model=RiskTierOverviewResponse)
def get_risk_tier_overview(
    session: Session = Depends(get_session),
) -> RiskTierOverviewResponse:
    """
    Return aggregate distribution of MCP servers across risk tiers.
    Returns total server count and per-tier counts with percentages.
    """
    # Query all servers grouped by risk_tier
    result = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    total = sum(row.count for row in result)

    tiers = []
    for row in result:
        percentage = (row.count / total * 100) if total > 0 else 0.0
        tiers.append(
            TierDistribution(
                tier=row.risk_tier or "UNKNOWN",
                count=row.count,
                percentage=round(percentage, 2),
            )
        )

    return RiskTierOverviewResponse(total_servers=total, tiers=tiers)


# ---------------------------------------------------------------------------
# Self-test using FastAPI TestClient with SQLite dependency override
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # Create in-memory SQLite for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build test app with router
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    # Seed test data
    db = TestingSessionLocal()
    try:
        # Add servers with different risk tiers
        servers = [
            McpServerRegistry(server_id="srv1", name="Server 1", risk_tier="TRUSTED_GENERAL"),
            McpServerRegistry(server_id="srv2", name="Server 2", risk_tier="TRUSTED_GENERAL"),
            McpServerRegistry(server_id="srv3", name="Server 3", risk_tier="TRUSTED_RESEARCH"),
            McpServerRegistry(server_id="srv4", name="Server 4", risk_tier="TRUSTED_RESEARCH"),
            McpServerRegistry(server_id="srv5", name="Server 5", risk_tier="ENTERPRISE_CONTROLLED"),
            McpServerRegistry(server_id="srv6", name="Server 6", risk_tier="ENTERPRISE_CONTROLLED"),
            McpServerRegistry(server_id="srv7", name="Server 7", risk_tier="CAUTION_LIMITED"),
            McpServerRegistry(server_id="srv8", name="Server 8", risk_tier="HIGH_RISK_ISOLATED"),
            McpServerRegistry(server_id="srv9", name="Server 9", risk_tier=None),  # Edge case: null tier
        ]
        db.add_all(servers)
        db.commit()

        # Test the endpoint
        response = client.get("/api/risk/overview")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert data["total_servers"] == 9, f"Expected 9 servers, got {data['total_servers']}"
        assert len(data["tiers"]) == 5, f"Expected 5 tier entries, got {len(data['tiers'])}"

        # Verify tier counts
        tier_counts = {t["tier"]: t["count"] for t in data["tiers"]}
        assert tier_counts.get("TRUSTED_GENERAL") == 2, f"Expected 2 TRUSTED_GENERAL, got {tier_counts.get('TRUSTED_GENERAL')}"
        assert tier_counts.get("TRUSTED_RESEARCH") == 2, f"Expected 2 TRUSTED_RESEARCH, got {tier_counts.get('TRUSTED_RESEARCH')}"
        assert tier_counts.get("ENTERPRISE_CONTROLLED") == 2, f"Expected 2 ENTERPRISE_CONTROLLED, got {tier_counts.get('ENTERPRISE_CONTROLLED')}"
        assert tier_counts.get("CAUTION_LIMITED") == 1, f"Expected 1 CAUTION_LIMITED, got {tier_counts.get('CAUTION_LIMITED')}"
        assert tier_counts.get("HIGH_RISK_ISOLATED") == 1, f"Expected 1 HIGH_RISK_ISOLATED, got {tier_counts.get('HIGH_RISK_ISOLATED')}"
        assert tier_counts.get("UNKNOWN") == 1, f"Expected 1 UNKNOWN (null tier), got {tier_counts.get('UNKNOWN')}"

        # Verify percentages sum to ~100%
        total_pct = sum(t["percentage"] for t in data["tiers"])
        assert 99.0 <= total_pct <= 101.0, f"Percentages should sum to ~100, got {total_pct}"

        print("PASS")

    finally:
        db.close()
        engine.dispose()

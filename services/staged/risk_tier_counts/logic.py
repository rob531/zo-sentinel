"""Risk tier counts service logic."""
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class TierCount(BaseModel):
    tier: str
    count: int


def get_risk_tier_counts(session: Session) -> list[dict[str, Any]]:
    """Return current count of servers grouped by risk_tier."""
    stmt = (
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .group_by(McpServerRegistry.risk_tier)
    )
    results = session.execute(stmt).all()
    return [{"tier": row.risk_tier, "count": row.count} for row in results]


def health(session: Session) -> dict[str, Any]:
    """Return health status for the risk tier counts service."""
    tiers = get_risk_tier_counts(session)
    return {"status": "ok", "tier_count": len(tiers)}


if __name__ == "__main__":
    import sys
    from io import StringIO

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Capture stdout
    output = StringIO()
    sys.stdout = output

    # Build in-memory SQLite test app
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Import app to get metadata and create tables
    import app.models
    from app.db import get_session as real_get_session

    app.models.Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed 3 servers with distinct tiers
    db = TestingSessionLocal()
    try:
        db.add(
            McpServerRegistry(
                server_id="srv-001",
                name="Trusted Server",
                risk_tier="TRUSTED_GENERAL",
                registry_source="test",
                url="http://trusted.example.com",
            )
        )
        db.add(
            McpServerRegistry(
                server_id="srv-002",
                name="Enterprise Server",
                risk_tier="ENTERPRISE_CONTROLLED",
                registry_source="test",
                url="http://enterprise.example.com",
            )
        )
        db.add(
            McpServerRegistry(
                server_id="srv-003",
                name="Caution Server",
                risk_tier="CAUTION_LIMITED",
                registry_source="test",
                url="http://caution.example.com",
            )
        )
        db.commit()
    finally:
        db.close()

    # Create test app with dependency override
    test_app = FastAPI()

    @test_app.get("/api/risk/tier-counts")
    def get_tiers():
        db = next(override_get_session())
        try:
            tiers = get_risk_tier_counts(db)
            return {"tiers": tiers}
        finally:
            db.close()

    # Override dependency
    test_app.dependency_overrides[real_get_session] = override_get_session
    client = TestClient(test_app)

    # Test
    response = client.get("/api/risk/tier-counts")
    data = response.json()

    sys.stdout = sys.__stdout__

    # Assertions
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    tiers = data.get("tiers", [])
    assert len(tiers) >= 3, f"Expected >= 3 tiers, got {len(tiers)}"

    # Verify the 3 seeded tiers are present
    tier_names = {t["tier"] for t in tiers}
    expected_tiers = {"TRUSTED_GENERAL", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED"}
    assert expected_tiers.issubset(tier_names), f"Missing expected tiers in {tier_names}"

    print("PASS")
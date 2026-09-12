from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import Dict, List
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

class RiskTierStatistics(BaseModel):
    total_servers: int
    risk_tier_counts: Dict[str, int]
    risk_tier_percentages: Dict[str, float]

def get_risk_tier_statistics(db: Session = Depends(get_session)) -> RiskTierStatistics:
    # Join McpServerRegistry with McpLlmAxisScore to get servers with risk tiers
    subquery = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier
        )
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            isouter=True
        )
        .subquery()
    )

    # Count servers by risk tier
    query = (
        select(
            subquery.c.risk_tier,
            func.count(subquery.c.server_id).label("count")
        )
        .group_by(subquery.c.risk_tier)
    )

    result = db.execute(query).fetchall()

    # Calculate total servers
    total_servers = sum(count for _, count in result)

    # Calculate risk tier counts and percentages
    risk_tier_counts = {tier: count for tier, count in result}
    risk_tier_percentages = {
        tier: (count / total_servers) * 100
        for tier, count in risk_tier_counts.items()
    }

    return RiskTierStatistics(
        total_servers=total_servers,
        risk_tier_counts=risk_tier_counts,
        risk_tier_percentages=risk_tier_percentages
    )

def create_app():
    app = FastAPI()

    @app.get("/api/risk/statistics", response_model=RiskTierStatistics)
    async def statistics(db: Session = Depends(get_session)):
        return get_risk_tier_statistics(db)

    return app

if __name__ == "__main__":
    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Create a test session
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_db = SessionLocal()

    # Seed test data
    test_servers = [
        McpServerRegistry(
            server_id="server1",
            risk_tier="low",
            name="Test Server 1",
            url="http://example.com/server1",
            confidence=0.9,
            trust_score=0.8,
            verdict="safe",
            verdict_reasoning="Test reasoning 1"
        ),
        McpServerRegistry(
            server_id="server2",
            risk_tier="medium",
            name="Test Server 2",
            url="http://example.com/server2",
            confidence=0.8,
            trust_score=0.7,
            verdict="safe",
            verdict_reasoning="Test reasoning 2"
        ),
        McpServerRegistry(
            server_id="server3",
            risk_tier="high",
            name="Test Server 3",
            url="http://example.com/server3",
            confidence=0.7,
            trust_score=0.6,
            verdict="safe",
            verdict_reasoning="Test reasoning 3"
        )
    ]

    test_db.add_all(test_servers)
    test_db.commit()

    # Create a test app with dependency overrides
    test_app = create_app()
    test_app.dependency_overrides[get_session] = lambda: test_db

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    response = client.get("/api/risk/statistics")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert data["risk_tier_counts"]["low"] == 1

    print("PASS")
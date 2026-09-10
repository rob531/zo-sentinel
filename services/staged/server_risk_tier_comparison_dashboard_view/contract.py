from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import List
import pytest

class RiskTierComparison(BaseModel):
    tier: str
    count: int
    percentage: float

app = FastAPI()

def get_risk_tier_comparison(session):
    query = (
        session.query(
            McpServerRegistry.risk_tier,
            McpServerRegistry.server_id
        )
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id
        )
        .group_by(McpServerRegistry.risk_tier)
    )

    results = session.execute(query).fetchall()

    total_servers = sum(row[1] for row in results)

    comparison_data = []
    for row in results:
        tier, count = row
        percentage = (count / total_servers) * 100
        comparison_data.append(RiskTierComparison(tier=tier, count=count, percentage=percentage))

    return comparison_data

@app.get("/api/dashboard/risk-tier-comparison", response_model=List[RiskTierComparison])
async def get_risk_tier_comparison_endpoint(session = Depends(get_session)):
    return get_risk_tier_comparison(session)

def seed_data(session):
    servers = [
        McpServerRegistry(server_id=f"server_{i}", risk_tier=f"tier_{i % 3}") for i in range(6)
    ]
    session.add_all(servers)
    session.commit()

    for server in servers:
        axis_scores = [
            McpLlmAxisScore(server_id=server.server_id, axis_name=f"axis_{i}", p_critical=0.1, p_danger=0.2, p_top=0.3) for i in range(3)
        ]
        session.add_all(axis_scores)
    session.commit()

@pytest.fixture
def test_app():
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        yield client

def test_risk_tier_comparison(test_app):
    response = test_app.get("/api/dashboard/risk-tier-comparison")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    for item in data:
        assert "tier" in item
        assert "count" in item
        assert "percentage" in item
    print("PASS")

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            seed_data(db)
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    uvicorn.run(app, host="127.0.0.1", port=8000)
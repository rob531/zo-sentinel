from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

class TierSummary(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierSummaryDashboard(BaseModel):
    total_servers: int
    tier_summary: List[TierSummary]

@router.get("/api/risk/summary/dashboard", response_model=RiskTierSummaryDashboard)
async def get_risk_tier_summary_dashboard(session: Session = Depends(get_session)):
    # Query to get the count of servers in each risk tier
    tier_counts = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label('count')
    ).group_by(McpServerRegistry.risk_tier).all()

    total_servers = sum(count for _, count in tier_counts)
    tier_summary = []

    for tier, count in tier_counts:
        percentage = (count / total_servers) * 100 if total_servers > 0 else 0
        tier_summary.append(TierSummary(tier=tier, count=count, percentage=percentage))

    return RiskTierSummaryDashboard(total_servers=total_servers, tier_summary=tier_summary)

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create the tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Seed the database with test data
    db = TestingSessionLocal()
    test_servers = [
        McpServerRegistry(server_id="server1", risk_tier="low"),
        McpServerRegistry(server_id="server2", risk_tier="medium"),
        McpServerRegistry(server_id="server3", risk_tier="high"),
    ]
    db.add_all(test_servers)
    db.commit()

    # Create a FastAPI app for testing
    app = FastAPI()
    app.include_router(router)

    # Override the get_session dependency
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Run the test
    import requests
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

    # Make a request to the endpoint
    response = requests.get("http://127.0.0.1:8000/api/risk/summary/dashboard")

    # Assert the response
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert len(data["tier_summary"]) == 3
    for tier in data["tier_summary"]:
        if tier["tier"] == "low":
            assert tier["count"] == 1
            assert tier["percentage"] == 100/3
        elif tier["tier"] == "medium":
            assert tier["count"] == 1
            assert tier["percentage"] == 100/3
        elif tier["tier"] == "high":
            assert tier["count"] == 1
            assert tier["percentage"] == 100/3

    print("PASS")
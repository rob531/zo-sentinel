from sqlalchemy.pool import StaticPool
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm import Session

router = APIRouter()

class ServerRiskEntry(BaseModel):
    server_id: int
    name: str
    verdict: str
    p_danger: float
    p_critical: float
    risk_tier: str
    last_assessed: datetime

class TopRiskServersResponse(BaseModel):
    servers: List[ServerRiskEntry]

def calculate_risk_tier(p_danger: float, p_critical: float) -> str:
    if p_danger >= 0.9:
        return "Extreme"
    elif p_danger >= 0.7 or p_critical >= 0.8:
        return "High"
    elif p_danger >= 0.5 or p_critical >= 0.6:
        return "Medium"
    elif p_danger >= 0.3 or p_critical >= 0.4:
        return "Low"
    else:
        return "Minimal"

@router.get("/api/servers/top-risk", response_model=TopRiskServersResponse)
async def get_top_risk_servers(
    days: int = Query(30, description="Number of days to look back for assessments"),
    limit: int = Query(20, description="Maximum number of servers to return"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    db: Session = Depends(get_session)
):
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    subquery = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.assessed_at).label("latest_assessment")
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    query = (
        db.query(
            McpServerRegistry.id.label("server_id"),
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            func.sum(McpLlmAxisScore.p_danger * 100 + McpLlmAxisScore.p_critical * 50).label("composite_risk"),
            func.sum(McpLlmAxisScore.p_danger).label("p_danger"),
            func.sum(McpLlmAxisScore.p_critical).label("p_critical"),
            func.max(McpLlmAxisScore.assessed_at).label("last_assessed")
        )
        .join(
            McpLlmAxisScore,
            and_(
                McpServerRegistry.id == McpLlmAxisScore.server_id,
                McpLlmAxisScore.assessed_at == subquery.c.latest_assessment,
                McpLlmAxisScore.assessed_at >= cutoff_date
            )
        )
        .group_by(
            McpServerRegistry.id,
            McpServerRegistry.name,
            McpServerRegistry.verdict
        )
    )

    if risk_tier:
        query = query.having(
            calculate_risk_tier(func.sum(McpLlmAxisScore.p_danger), func.sum(McpLlmAxisScore.p_critical)) == risk_tier
        )

    results = query.order_by(desc("composite_risk")).limit(limit).all()

    servers = []
    for row in results:
        risk_tier = calculate_risk_tier(row.p_danger, row.p_critical)
        servers.append(
            ServerRiskEntry(
                server_id=row.server_id,
                name=row.name,
                verdict=row.verdict,
                p_danger=row.p_danger,
                p_critical=row.p_critical,
                risk_tier=risk_tier,
                last_assessed=row.last_assessed
            )
        )

    return TopRiskServersResponse(servers=servers)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Create test data
    from datetime import datetime, timedelta

    # Add test servers
    test_servers = [
        {"id": 1, "name": "Server A", "verdict": "Clean"},
        {"id": 2, "name": "Server B", "verdict": "Suspicious"},
        {"id": 3, "name": "Server C", "verdict": "Malicious"},
        {"id": 4, "name": "Server D", "verdict": "Clean"},
        {"id": 5, "name": "Server E", "verdict": "Suspicious"},
    ]

    for server in test_servers:
        test_session.add(McpServerRegistry(**server))

    # Add test scores
    test_scores = [
        {"server_id": 1, "p_danger": 0.95, "p_critical": 0.1, "assessed_at": datetime.utcnow() - timedelta(days=1)},
        {"server_id": 1, "p_danger": 0.9, "p_critical": 0.2, "assessed_at": datetime.utcnow() - timedelta(days=2)},
        {"server_id": 2, "p_danger": 0.85, "p_critical": 0.3, "assessed_at": datetime.utcnow() - timedelta(days=1)},
        {"server_id": 3, "p_danger": 0.75, "p_critical": 0.4, "assessed_at": datetime.utcnow() - timedelta(days=1)},
        {"server_id": 4, "p_danger": 0.65, "p_critical": 0.5, "assessed_at": datetime.utcnow() - timedelta(days=1)},
        {"server_id": 5, "p_danger": 0.55, "p_critical": 0.6, "assessed_at": datetime.utcnow() - timedelta(days=1)},
    ]

    for score in test_scores:
        test_session.add(McpLlmAxisScore(**score))

    test_session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/servers/top-risk")
    assert response.status_code == 200
    data = response.json()

    # Verify results
    assert len(data["servers"]) == 5
    assert data["servers"][0]["p_danger"] == 1.85  # 0.95 + 0.9
    assert data["servers"][0]["server_id"] == 1
    assert data["servers"][0]["name"] == "Server A"
    assert data["servers"][0]["verdict"] == "Clean"
    assert data["servers"][0]["risk_tier"] == "Extreme"
    assert data["servers"][0]["last_assessed"] is not None

    # Verify sorting
    for i in range(len(data["servers"]) - 1):
        assert data["servers"][i]["p_danger"] >= data["servers"][i+1]["p_danger"]

    print("PASS")
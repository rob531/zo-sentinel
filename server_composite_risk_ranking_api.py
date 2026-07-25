from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import asc, nullslast

router = APIRouter()

class ServerRiskRanking(BaseModel):
    server_id: int
    name: str
    url: str
    trust_score: Optional[float]
    risk_tier: Optional[str]
    last_scanned: Optional[str]
    verdict: Optional[str]

class RiskRankingResponse(BaseModel):
    servers: List[ServerRiskRanking]
    total: int

def get_composite_risk_score(server_id: int, session: Session) -> float:
    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": f"""
                SELECT
                    COALESCE(llm_axis_scores.trust_score, 0) +
                    COALESCE(signal_scores.risk_score, 0) AS composite_risk
                FROM
                    mcp_llm_axis_scores
                LEFT JOIN
                    mcp_signal_scores ON mcp_llm_axis_scores.server_id = mcp_signal_scores.server_id
                WHERE
                    mcp_llm_axis_scores.server_id = {server_id}
                LIMIT 1
                """
            }
        )
        response.raise_for_status()
        result = response.json()
        return result[0]['composite_risk'] if result else 0.0
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/ranked-by-risk", response_model=RiskRankingResponse)
async def get_servers_ranked_by_risk(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    risk_tier: Optional[str] = None,
    session: Session = Depends(get_session)
):
    query = session.query(MCPServerRegistry)

    if risk_tier:
        query = query.filter(MCPServerRegistry.risk_tier == risk_tier)

    servers = query.order_by(
        asc(nullslast(MCPServerRegistry.trust_score)),
        MCPServerRegistry.risk_tier
    ).limit(limit).offset(offset).all()

    total = query.count()

    result = []
    for server in servers:
        composite_risk = get_composite_risk_score(server.id, session)
        result.append({
            "server_id": server.id,
            "name": server.name,
            "url": server.url,
            "trust_score": server.trust_score,
            "risk_tier": server.risk_tier,
            "last_scanned": server.last_scanned,
            "verdict": "safe" if composite_risk < 0.5 else "risky"
        })

    return {"servers": result, "total": total}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server1 = MCPServerRegistry(
        name="Test Server 1",
        url="http://test1.example.com",
        trust_score=0.8,
        risk_tier="low",
        last_scanned="2023-01-01"
    )
    test_server2 = MCPServerRegistry(
        name="Test Server 2",
        url="http://test2.example.com",
        trust_score=0.3,
        risk_tier="high",
        last_scanned="2023-01-02"
    )
    test_session.add_all([test_server1, test_server2])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/ranked-by-risk")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["servers"], list)
    assert len(data["servers"]) > 0
    assert all(field in data["servers"][0] for field in ["server_id", "name", "url", "trust_score", "risk_tier", "last_scanned", "verdict"])
    assert data["servers"][0]["risk_tier"] == "high"  # Should be ordered by risk_tier

    print("PASS")
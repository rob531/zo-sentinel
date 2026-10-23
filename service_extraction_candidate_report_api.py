from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import func, and_
from sqlalchemy.orm import joinedload
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class ServerExtractionCandidate(BaseModel):
    server_id: int
    hostname: str
    ip_address: str
    last_assessment_date: datetime
    risk_score: float
    llm_axis_scores: dict

class PaginatedResponse(BaseModel):
    total: int
    items: List[ServerExtractionCandidate]

@router.get("/servers/service-extraction-candidates", response_model=PaginatedResponse)
async def get_service_extraction_candidates(
    session=Depends(get_session),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    min_risk_score: Optional[float] = Query(None, ge=0, le=1),
    max_days_since_assessment: Optional[int] = Query(None, ge=0)
):
    query = session.query(MCPServerRegistry).options(
        joinedload(MCPServerRegistry.llm_axis_scores)
    )

    if min_risk_score is not None:
        query = query.filter(MCPServerRegistry.risk_score >= min_risk_score)

    if max_days_since_assessment is not None:
        cutoff_date = datetime.now() - timedelta(days=max_days_since_assessment)
        query = query.filter(MCPServerRegistry.last_assessment_date >= cutoff_date)

    total = query.count()
    servers = query.limit(limit).offset(offset).all()

    response_items = []
    for server in servers:
        llm_scores = {
            axis.axis_name: axis.score
            for axis in server.llm_axis_scores
        }
        response_items.append({
            "server_id": server.id,
            "hostname": server.hostname,
            "ip_address": server.ip_address,
            "last_assessment_date": server.last_assessment_date,
            "risk_score": server.risk_score,
            "llm_axis_scores": llm_scores
        })

    return {"total": total, "items": response_items}

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Test setup
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server1 = MCPServerRegistry(
        hostname="server1.example.com",
        ip_address="192.168.1.1",
        last_assessment_date=datetime.now() - timedelta(days=5),
        risk_score=0.8
    )
    test_server2 = MCPServerRegistry(
        hostname="server2.example.com",
        ip_address="192.168.1.2",
        last_assessment_date=datetime.now() - timedelta(days=15),
        risk_score=0.6
    )
    test_server3 = MCPServerRegistry(
        hostname="server3.example.com",
        ip_address="192.168.1.3",
        last_assessment_date=datetime.now() - timedelta(days=2),
        risk_score=0.9
    )
    test_session.add_all([test_server1, test_server2, test_server3])
    test_session.commit()

    # Add some LLM axis scores
    test_axis1 = MCPLLMAxisScores(
        server_id=test_server1.id,
        axis_name="security",
        score=0.7
    )
    test_axis2 = MCPLLMAxisScores(
        server_id=test_server1.id,
        axis_name="performance",
        score=0.8
    )
    test_axis3 = MCPLLMAxisScores(
        server_id=test_server2.id,
        axis_name="security",
        score=0.5
    )
    test_session.add_all([test_axis1, test_axis2, test_axis3])
    test_session.commit()

    client = TestClient(app)

    # Test 1: Basic pagination
    response = client.get("/servers/service-extraction-candidates?limit=2")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 2

    # Test 2: Filter by risk score
    response = client.get("/servers/service-extraction-candidates?min_risk_score=0.7")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert all(item["risk_score"] >= 0.7 for item in response.json()["items"])

    # Test 3: Filter by assessment date
    response = client.get("/servers/service-extraction-candidates?max_days_since_assessment=10")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert all(
        (datetime.now() - datetime.strptime(item["last_assessment_date"], "%Y-%m-%dT%H:%M:%S.%f")).days <= 10
        for item in response.json()["items"]
    )

    print("PASS")
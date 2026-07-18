from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry, Org, User
import requests
from pydantic import BaseModel

router = APIRouter()

class AxisScore(BaseModel):
    axis: str
    score: float
    rationale: Optional[str]

class ServerScoreResponse(BaseModel):
    server_id: int
    server_name: str
    org_name: str
    scores: List[AxisScore]
    overall_score: float

def get_write_service_data(query: str) -> List[Dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Write service error")
    return response.json()

@router.get("/scores/{server_id}", response_model=ServerScoreResponse)
def get_server_scores(server_id: int, session: Session = Depends(get_session)):
    # Get server details
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get organization name
    org = session.query(Org).filter(Org.id == server.org_id).first()
    org_name = org.name if org else "Unknown"

    # Get axis scores
    axis_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    # Calculate overall score
    overall_score = sum(getattr(score, axis) for score in axis_scores for axis in
                        ['overall_risk', 'auth_strength', 'capability_breadth',
                         'data_sensitivity', 'network_egress', 'maintainer_trust',
                         'exploit_surface']) / 7

    # Prepare response
    scores = []
    for axis in ['overall_risk', 'auth_strength', 'capability_breadth',
                 'data_sensitivity', 'network_egress', 'maintainer_trust',
                 'exploit_surface']:
        score_value = getattr(axis_scores[0], axis) if axis_scores else 0.0
        scores.append(AxisScore(axis=axis, score=score_value, rationale=None))

    return ServerScoreResponse(
        server_id=server_id,
        server_name=server.name,
        org_name=org_name,
        scores=scores,
        overall_score=overall_score
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_org = Org(name="Test Org")
    test_session.add(test_org)
    test_session.commit()

    test_server = MCPServerRegistry(name="Test Server", org_id=test_org.id)
    test_session.add(test_server)
    test_session.commit()

    test_scores = MCPLLMAxisScores(
        server_id=test_server.id,
        overall_risk=0.8,
        auth_strength=0.6,
        capability_breadth=0.7,
        data_sensitivity=0.9,
        network_egress=0.5,
        maintainer_trust=0.8,
        exploit_surface=0.7
    )
    test_session.add(test_scores)
    test_session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get(f"/scores/{test_server.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server.id
    assert data["server_name"] == "Test Server"
    assert data["org_name"] == "Test Org"
    assert len(data["scores"]) == 7
    assert data["overall_score"] == pytest.approx(0.714, 0.001)

    print("PASS")
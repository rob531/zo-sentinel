from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
from pydantic import BaseModel
import requests

router = APIRouter()

class AdversarialDescriptionProbeResponse(BaseModel):
    server_id: int
    server_name: str
    overall_risk: float
    auth_strength: float
    capability_breadth: float
    data_sensitivity: float
    network_egress: float
    maintainer_trust: float
    exploit_surface: float
    disputes: List[Dict[str, Optional[str]]]

@router.get("/adversarial-description-probe/{server_id}", response_model=AdversarialDescriptionProbeResponse)
async def get_adversarial_description_probe(
    server_id: int,
    session: Session = Depends(get_session)
) -> AdversarialDescriptionProbeResponse:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).first()
    if not scores:
        raise HTTPException(status_code=404, detail="Scores not found for server")

    disputes = session.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id == server_id).all()
    dispute_list = [
        {"field": dispute.field, "comment": dispute.comment, "resolved": dispute.resolved}
        for dispute in disputes
    ]

    return AdversarialDescriptionProbeResponse(
        server_id=server.id,
        server_name=server.name,
        overall_risk=scores.overall_risk,
        auth_strength=scores.auth_strength,
        capability_breadth=scores.capability_breadth,
        data_sensitivity=scores.data_sensitivity,
        network_egress=scores.network_egress,
        maintainer_trust=scores.maintainer_trust,
        exploit_surface=scores.exploit_surface,
        disputes=dispute_list
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test data
    with SessionLocal() as session:
        session.add(MCPServerRegistry(id=1, name="Test Server"))
        session.add(MCPLLMAxisScores(
            server_id=1,
            overall_risk=0.5,
            auth_strength=0.6,
            capability_breadth=0.7,
            data_sensitivity=0.8,
            network_egress=0.9,
            maintainer_trust=0.4,
            exploit_surface=0.3
        ))
        session.add(MCPScoreDisputes(
            server_id=1,
            field="overall_risk",
            comment="Disputed risk score",
            resolved=False
        ))
        session.commit()

    client = TestClient(app)

    # Test endpoint
    response = client.get("/adversarial-description-probe/1")
    assert response.status_code == 200
    assert response.json()["server_id"] == 1
    assert response.json()["server_name"] == "Test Server"
    assert response.json()["overall_risk"] == 0.5
    assert len(response.json()["disputes"]) == 1
    assert response.json()["disputes"][0]["field"] == "overall_risk"

    print("PASS")
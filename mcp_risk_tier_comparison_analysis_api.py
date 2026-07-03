from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores

router = APIRouter()

class TierComparison(BaseModel):
    count: int
    percentage: float

class AxisComparison(BaseModel):
    label: str
    p_top: float

class ComparisonResponse(BaseModel):
    tier1: TierComparison
    tier2: TierComparison
    comparison: Dict[str, Dict[str, AxisComparison]]

@router.get("/comparison/risk-tier", response_model=ComparisonResponse)
def get_risk_tier_comparison(tier1: str, tier2: str, session: Session = Depends(get_session)):
    total_servers = session.query(func.count(MCPServerRegistry.id)).scalar()

    tier1_count = session.query(func.count(MCPServerRegistry.id)).filter(MCPServerRegistry.risk_tier == tier1).scalar()
    tier2_count = session.query(func.count(MCPServerRegistry.id)).filter(MCPServerRegistry.risk_tier == tier2).scalar()

    if total_servers == 0:
        raise HTTPException(status_code=404, detail="No servers found")

    tier1_percentage = (tier1_count / total_servers) * 100
    tier2_percentage = (tier2_count / total_servers) * 100

    axes = ['axis1', 'axis2', 'axis3']
    comparison = {}

    for axis in axes:
        tier1_scores = session.query(MCPAxisScores).join(MCPServerRegistry).filter(MCPServerRegistry.risk_tier == tier1, MCPAxisScores.axis == axis).all()
        tier2_scores = session.query(MCPAxisScores).join(MCPServerRegistry).filter(MCPServerRegistry.risk_tier == tier2, MCPAxisScores.axis == axis).all()

        tier1_label = f"{axis}_label"
        tier2_label = f"{axis}_label"

        tier1_p_top = sum(score.p_top for score in tier1_scores) / len(tier1_scores) if tier1_scores else 0
        tier2_p_top = sum(score.p_top for score in tier2_scores) / len(tier2_scores) if tier2_scores else 0

        comparison[axis] = {
            'tier1': AxisComparison(label=tier1_label, p_top=tier1_p_top),
            'tier2': AxisComparison(label=tier2_label, p_top=tier2_p_top)
        }

    return {
        'tier1': TierComparison(count=tier1_count, percentage=tier1_percentage),
        'tier2': TierComparison(count=tier2_count, percentage=tier2_percentage),
        'comparison': comparison
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(app)

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    session = TestingSessionLocal()
    server1 = MCPServerRegistry(risk_tier="tier1")
    server2 = MCPServerRegistry(risk_tier="tier1")
    server3 = MCPServerRegistry(risk_tier="tier2")
    server4 = MCPServerRegistry(risk_tier="tier2")
    server5 = MCPServerRegistry(risk_tier="tier2")

    session.add_all([server1, server2, server3, server4, server5])
    session.commit()

    axis_scores = [
        MCPAxisScores(server_id=server1.id, axis="axis1", p_top=0.8),
        MCPAxisScores(server_id=server1.id, axis="axis2", p_top=0.7),
        MCPAxisScores(server_id=server1.id, axis="axis3", p_top=0.6),
        MCPAxisScores(server_id=server2.id, axis="axis1", p_top=0.7),
        MCPAxisScores(server_id=server2.id, axis="axis2", p_top=0.6),
        MCPAxisScores(server_id=server2.id, axis="axis3", p_top=0.5),
        MCPAxisScores(server_id=server3.id, axis="axis1", p_top=0.6),
        MCPAxisScores(server_id=server3.id, axis="axis2", p_top=0.5),
        MCPAxisScores(server_id=server3.id, axis="axis3", p_top=0.4),
        MCPAxisScores(server_id=server4.id, axis="axis1", p_top=0.5),
        MCPAxisScores(server_id=server4.id, axis="axis2", p_top=0.4),
        MCPAxisScores(server_id=server4.id, axis="axis3", p_top=0.3),
        MCPAxisScores(server_id=server5.id, axis="axis1", p_top=0.4),
        MCPAxisScores(server_id=server5.id, axis="axis2", p_top=0.3),
        MCPAxisScores(server_id=server5.id, axis="axis3", p_top=0.2)
    ]

    session.add_all(axis_scores)
    session.commit()

    response = client.get("/comparison/risk-tier?tier1=tier1&tier2=tier2")
    assert response.status_code == 200
    data = response.json()

    assert data["tier1"]["count"] == 2
    assert data["tier2"]["count"] == 3
    assert data["tier1"]["percentage"] == 40.0
    assert data["tier2"]["percentage"] == 60.0

    assert "axis1" in data["comparison"]
    assert "axis2" in data["comparison"]
    assert "axis3" in data["comparison"]

    print("PASS")
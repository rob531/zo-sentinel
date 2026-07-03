from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores

router = APIRouter()

class AxisDetail(BaseModel):
    label: str
    p_top: float

class ServerDetail(BaseModel):
    server_id: str
    name: str
    axes: Dict[str, AxisDetail]

class TierDetail(BaseModel):
    tier: str
    servers: List[ServerDetail]

@router.get("/detail/risk-tier", response_model=TierDetail)
async def get_risk_tier_detail(tier: str, db: Session = Depends(get_session)):
    # Get servers for the specified tier
    servers = db.query(MCPServerRegistry).filter(MCPServerRegistry.risk_tier == tier).all()

    if not servers:
        raise HTTPException(status_code=404, detail="No servers found for the specified tier")

    # Get axis scores for each server
    server_details = []
    for server in servers:
        axes = db.query(MCPAxisScores).filter(MCPAxisScores.server_id == server.server_id).all()
        axes_dict = {
            axis.axis: AxisDetail(label=axis.label, p_top=axis.p_top)
            for axis in axes
        }
        server_details.append(
            ServerDetail(
                server_id=server.server_id,
                name=server.name,
                axes=axes_dict
            )
        )

    return TierDetail(tier=tier, servers=server_details)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPAxisScores
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create a test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed the test database
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(server_id="1", name="Server 1", risk_tier="high"),
        MCPServerRegistry(server_id="2", name="Server 2", risk_tier="high"),
        MCPServerRegistry(server_id="3", name="Server 3", risk_tier="high"),
        MCPAxisScores(server_id="1", axis="axis1", label="Label 1", p_top=0.9),
        MCPAxisScores(server_id="1", axis="axis2", label="Label 2", p_top=0.8),
        MCPAxisScores(server_id="2", axis="axis1", label="Label 1", p_top=0.7),
        MCPAxisScores(server_id="2", axis="axis2", label="Label 2", p_top=0.6),
        MCPAxisScores(server_id="3", axis="axis1", label="Label 1", p_top=0.5),
        MCPAxisScores(server_id="3", axis="axis2", label="Label 2", p_top=0.4),
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/detail/risk-tier?tier=high")
    assert response.status_code == 200
    data = response.json()
    assert data["tier"] == "high"
    assert len(data["servers"]) == 3
    for server in data["servers"]:
        assert "server_id" in server
        assert "name" in server
        assert "axes" in server
        for axis in server["axes"].values():
            assert "label" in axis
            assert "p_top" in axis

    print("PASS")
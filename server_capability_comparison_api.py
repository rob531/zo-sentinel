from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

router = APIRouter()

class RiskAxis(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float

class ServerComparison(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    overall_risk: float
    axes: List[RiskAxis]

class ComparisonResponse(BaseModel):
    servers: List[ServerComparison]
    metadata: dict

AXIS_ORDER = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface"
]

@router.get("/servers/compare", response_model=ComparisonResponse)
async def compare_servers(
    server_ids: str,
    db: Session = Depends(get_session)
):
    server_id_list = server_ids.split(',')
    if len(server_id_list) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least 2 server_ids must be provided"
        )

    servers = []
    metadata = {"warnings": []}

    for server_id in server_id_list:
        server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
        if not server:
            metadata["warnings"].append(f"Server {server_id} not found")
            continue

        axis_scores = db.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).all()
        if not axis_scores:
            metadata["warnings"].append(f"No axis scores found for server {server_id}")
            continue

        axes = []
        for axis in AXIS_ORDER:
            score = next((s for s in axis_scores if s.axis_name == axis), None)
            if score:
                axes.append(RiskAxis(
                    axis_name=score.axis_name,
                    label=score.label,
                    p_top=score.p_top,
                    p_critical=score.p_critical
                ))
            else:
                metadata["warnings"].append(f"Missing axis {axis} for server {server_id}")

        if axes:
            servers.append(ServerComparison(
                server_id=server.server_id,
                name=server.name,
                risk_tier=server.risk_tier,
                overall_risk=next((s.p_top for s in axis_scores if s.axis_name == "overall_risk"), 0.0),
                axes=axes
            ))

    return ComparisonResponse(servers=servers, metadata=metadata)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_db = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_db)
    Base.metadata.create_all(bind=test_db)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(app)

    with SessionLocal() as db:
        db.add(McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="high"
        ))
        db.add(McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="medium"
        ))
        for server_id in ["server1", "server2"]:
            for axis in AXIS_ORDER:
                db.add(McpLlmAxisScores(
                    server_id=server_id,
                    axis_name=axis,
                    label=f"Label for {axis}",
                    p_top=0.5,
                    p_critical=0.3
                ))
        db.commit()

    response = test_client.get("/servers/compare?server_ids=server1,server2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) == 2
    for server in data["servers"]:
        assert len(server["axes"]) == 7
    print("PASS")
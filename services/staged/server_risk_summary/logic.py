from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional, List
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class RiskSummaryResponse(BaseModel):
    server_id: str
    overall_risk: float
    risk_tier: str
    axes: Optional[Dict[str, AxisScore]] = None

def get_server_risk_summary(
    server_id: str,
    include_axes: bool = True,
    db: Session = Depends(get_session)
) -> RiskSummaryResponse:
    # Get overall risk and tier from server registry
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores if requested
    axes = {}
    if include_axes:
        axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
        for score in axis_scores:
            axes[score.axis_name] = {
                "label": score.label,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger
            }

    return RiskSummaryResponse(
        server_id=server.server_id,
        overall_risk=server.overall_risk,
        risk_tier=server.risk_tier,
        axes=axes if include_axes else None
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Mock data
    test_server_id = "srv-123"
    test_server = McpServerRegistry(
        server_id=test_server_id,
        overall_risk=85,
        risk_tier="TRUSTED_GENERAL"
    )

    test_axes = [
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis1",
            label="Test Axis 1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis2",
            label="Test Axis 2",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis3",
            label="Test Axis 3",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5
        )
    ]

    # Insert test data
    with TestSession() as session:
        session.add(test_server)
        session.add_all(test_axes)
        session.commit()

    # Test endpoint
    @app.get("/api/server/{server_id}/risk_summary")
    async def test_endpoint(server_id: str, include_axes: bool = True):
        return get_server_risk_summary(server_id, include_axes)

    client = TestClient(app)
    response = client.get(f"/api/server/{test_server_id}/risk_summary")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"] == 85
    assert data["risk_tier"] == "TRUSTED_GENERAL"
    assert len(data["axes"]) == 3
    assert data["axes"]["axis1"]["label"] == "Test Axis 1"

    print("PASS")
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
from app.db import get_session
from sqlalchemy.orm import Session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

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

@router.get("/server/{server_id}/risk_summary", response_model=RiskSummaryResponse)
async def get_server_risk_summary(
    server_id: str,
    include_axes: bool = True,
    session: Session = Depends(get_session)
):
    # Get overall risk and tier from server registry
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores if requested
    axes = {}
    if include_axes:
        axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
        for score in axis_scores:
            axes[score.axis_name] = {
                "label": score.label,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger
            }

    return {
        "server_id": server.server_id,
        "overall_risk": server.overall_risk,
        "risk_tier": server.risk_tier,
        "axes": axes if include_axes else None
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    # Override get_session for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Insert test data
    with SessionLocal() as session:
        test_server = McpServerRegistry(
            server_id="srv-123",
            overall_risk=85,
            risk_tier="TRUSTED_GENERAL"
        )
        session.add(test_server)

        test_axes = [
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="security",
                label="Security Risk",
                p_top=0.9,
                p_critical=0.8,
                p_danger=0.7
            ),
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="performance",
                label="Performance Risk",
                p_top=0.85,
                p_critical=0.75,
                p_danger=0.65
            ),
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="reliability",
                label="Reliability Risk",
                p_top=0.95,
                p_critical=0.85,
                p_danger=0.75
            )
        ]
        session.add_all(test_axes)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/server/srv-123/risk_summary")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"] == 85
    assert data["risk_tier"] == "TRUSTED_GENERAL"
    assert len(data["axes"]) == 3
    assert data["axes"]["security"]["label"] == "Security Risk"

    print("PASS")
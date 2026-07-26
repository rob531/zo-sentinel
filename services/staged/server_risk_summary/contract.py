from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/server")

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

@router.get("/{server_id}/risk_summary", response_model=RiskSummaryResponse)
async def get_server_risk_summary(
    server_id: str,
    include_axes: bool = Query(True),
    db: Session = Depends(get_session)
):
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

    return {
        "server_id": server.server_id,
        "overall_risk": server.overall_risk,
        "risk_tier": server.risk_tier,
        "axes": axes if include_axes else None
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as db:
        test_server = McpServerRegistry(
            server_id="srv-123",
            overall_risk=85,
            risk_tier="TRUSTED_GENERAL"
        )
        db.add(test_server)

        test_axes = [
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="axis1",
                label="Test Axis 1",
                p_top=0.9,
                p_critical=0.8,
                p_danger=0.7
            ),
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="axis2",
                label="Test Axis 2",
                p_top=0.8,
                p_critical=0.7,
                p_danger=0.6
            ),
            McpLlmAxisScore(
                server_id="srv-123",
                axis_name="axis3",
                label="Test Axis 3",
                p_top=0.7,
                p_critical=0.6,
                p_danger=0.5
            )
        ]
        db.add_all(test_axes)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/server/srv-123/risk_summary")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"] == 85
    assert data["risk_tier"] == "TRUSTED_GENERAL"
    assert len(data["axes"]) == 3
    assert data["axes"]["axis1"]["label"] == "Test Axis 1"

    print("PASS")